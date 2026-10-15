import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    DDL,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
    func,
    literal_column,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AppointmentStatus(enum.StrEnum):
    scheduled = "scheduled"
    rescheduled = "rescheduled"
    completed = "completed"
    cancelled = "cancelled"


# Statuses that occupy a slot on the calendar.
ACTIVE_STATUSES = (AppointmentStatus.scheduled, AppointmentStatus.rescheduled)


class AppointmentType(enum.StrEnum):
    checkup = "checkup"
    follow_up = "follow_up"
    sick_visit = "sick_visit"
    physical = "physical"
    vaccination = "vaccination"
    new_patient = "new_patient"


class UserRole(enum.StrEnum):
    admin = "admin"
    patient = "patient"


class PendingActionKind(enum.StrEnum):
    schedule = "schedule"
    cancel = "cancel"
    reschedule = "reschedule"


def _enum_column(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    # Non-native enums stored as VARCHAR, with a CHECK constraint enforcing the allowed values.
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


# Postgres prerequisites for the appointment overlap constraint. `timestamptz + interval` is
# only STABLE, so wrap the minute arithmetic (which is timezone-independent) in an IMMUTABLE
# function that an exclusion constraint may use.
APPOINTMENT_END_FN = """
CREATE OR REPLACE FUNCTION appointment_end(start_at timestamptz, minutes integer)
RETURNS timestamptz
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$ SELECT start_at + make_interval(mins => minutes) $$
"""

event.listen(Base.metadata, "before_create", DDL("CREATE EXTENSION IF NOT EXISTS btree_gist"))
event.listen(Base.metadata, "before_create", DDL(APPOINTMENT_END_FN))


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (
        Index("uq_patients_email_lower", func.lower(literal_column("email")), unique=True),
        CheckConstraint("length(btrim(name)) > 0", name="ck_patients_name_not_blank"),
        CheckConstraint("position('@' in email) > 1", name="ck_patients_email_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    appointments: Mapped[list["Appointment"]] = relationship(
        back_populates="patient", cascade="all, delete-orphan", passive_deletes=True
    )
    user: Mapped["User | None"] = relationship(
        back_populates="patient", cascade="all, delete-orphan", passive_deletes=True
    )


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint(
            "duration_minutes BETWEEN 5 AND 240", name="ck_appointments_duration_range"
        ),
        ExcludeConstraint(
            (literal_column("patient_id"), "="),
            (
                literal_column(
                    "tstzrange(scheduled_at, appointment_end(scheduled_at, duration_minutes), '[)')"
                ),
                "&&",
            ),
            name="ex_appointments_patient_no_overlap",
            using="gist",
            where=literal_column("status IN ('scheduled', 'rescheduled')"),
        ),
        Index("ix_appointments_patient_id", "patient_id"),
        Index("ix_appointments_scheduled_at", "scheduled_at"),
        Index("ix_appointments_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    appointment_type: Mapped[AppointmentType] = mapped_column(
        _enum_column(AppointmentType, "appointment_type_enum"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(
        Integer, default=30, server_default="30", nullable=False
    )
    status: Mapped[AppointmentStatus] = mapped_column(
        _enum_column(AppointmentStatus, "appointment_status_enum"),
        default=AppointmentStatus.scheduled,
        server_default=AppointmentStatus.scheduled.value,
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(String(512))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    patient: Mapped["Patient"] = relationship(back_populates="appointments")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("uq_users_email_lower", func.lower(literal_column("email")), unique=True),
        CheckConstraint(
            "(role = 'patient' AND patient_id IS NOT NULL) "
            "OR (role = 'admin' AND patient_id IS NULL)",
            name="ck_users_role_patient_link",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(_enum_column(UserRole, "user_role_enum"), nullable=False)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="CASCADE"),
        unique=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    patient: Mapped["Patient | None"] = relationship(back_populates="user")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256 of the session token; the raw token only ever lives in the client's cookie.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatPendingAction(Base):
    """A write proposed in chat, held server-side until the user explicitly confirms it."""

    __tablename__ = "chat_pending_actions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[PendingActionKind] = mapped_column(
        _enum_column(PendingActionKind, "pending_action_kind_enum"), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
