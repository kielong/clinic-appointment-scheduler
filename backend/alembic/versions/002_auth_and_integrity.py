"""Auth tables and data-integrity constraints

Adds users, auth sessions, and chat pending actions; cleans existing rows and enforces
constraints that the initial schema left to the application (allowed enum values, unique
patient emails, valid durations, no overlapping active appointments per patient).

Revision ID: 002
Revises: 001
Create Date: 2026-10-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPOINTMENT_TYPES = ("checkup", "follow_up", "sick_visit", "physical", "vaccination", "new_patient")
APPOINTMENT_STATUSES = ("scheduled", "rescheduled", "completed", "cancelled")


def _in_list(column: str, values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION appointment_end(start_at timestamptz, minutes integer)
        RETURNS timestamptz
        LANGUAGE sql IMMUTABLE PARALLEL SAFE
        AS $$ SELECT start_at + make_interval(mins => minutes) $$
        """
    )

    # --- Clean existing data so the new constraints can be applied ---
    op.execute("UPDATE patients SET name = btrim(name), email = lower(btrim(email))")
    op.execute("UPDATE patients SET created_at = now() WHERE created_at IS NULL")
    op.execute(
        """
        DO $$
        DECLARE dup text;
        BEGIN
            SELECT email INTO dup FROM patients GROUP BY email HAVING count(*) > 1 LIMIT 1;
            IF dup IS NOT NULL THEN
                RAISE EXCEPTION 'Duplicate patient email %; merge these patients first', dup;
            END IF;
        END $$
        """
    )
    op.execute("UPDATE appointments SET duration_minutes = 30 WHERE duration_minutes IS NULL")
    op.execute(
        "UPDATE appointments SET duration_minutes = LEAST(GREATEST(duration_minutes, 5), 240)"
    )
    op.execute("UPDATE appointments SET status = 'scheduled' WHERE status IS NULL")
    op.execute("UPDATE appointments SET created_at = now() WHERE created_at IS NULL")
    # If a patient has overlapping active appointments, keep the earliest-booked one and cancel
    # the rest (with an audit note) so the exclusion constraint can be created.
    op.execute(
        """
        UPDATE appointments a
        SET status = 'cancelled',
            notes = concat_ws(
                E'\n',
                nullif(btrim(a.notes), ''),
                'Auto-cancelled during migration 002: overlapped another active appointment '
                || 'for this patient.'
            )
        WHERE a.status IN ('scheduled', 'rescheduled')
          AND EXISTS (
            SELECT 1 FROM appointments b
            WHERE b.patient_id = a.patient_id
              AND b.id <> a.id
              AND b.status IN ('scheduled', 'rescheduled')
              AND (b.created_at, b.id) < (a.created_at, a.id)
              AND tstzrange(b.scheduled_at, appointment_end(b.scheduled_at, b.duration_minutes))
                  && tstzrange(a.scheduled_at, appointment_end(a.scheduled_at, a.duration_minutes))
          )
        """
    )

    # --- patients ---
    op.alter_column("patients", "created_at", nullable=False)
    op.create_index("uq_patients_email_lower", "patients", [sa.text("lower(email)")], unique=True)
    op.create_check_constraint("ck_patients_name_not_blank", "patients", "length(btrim(name)) > 0")
    op.create_check_constraint("ck_patients_email_format", "patients", "position('@' in email) > 1")

    # --- appointments ---
    op.alter_column("appointments", "duration_minutes", nullable=False)
    op.alter_column("appointments", "status", nullable=False)
    op.alter_column("appointments", "created_at", nullable=False)
    op.create_check_constraint(
        "appointment_type_enum", "appointments", _in_list("appointment_type", APPOINTMENT_TYPES)
    )
    op.create_check_constraint(
        "appointment_status_enum", "appointments", _in_list("status", APPOINTMENT_STATUSES)
    )
    op.create_check_constraint(
        "ck_appointments_duration_range", "appointments", "duration_minutes BETWEEN 5 AND 240"
    )
    op.drop_constraint("appointments_patient_id_fkey", "appointments", type_="foreignkey")
    op.create_foreign_key(
        "appointments_patient_id_fkey",
        "appointments",
        "patients",
        ["patient_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_appointments_patient_id", "appointments", ["patient_id"])
    op.create_index("ix_appointments_scheduled_at", "appointments", ["scheduled_at"])
    op.create_index("ix_appointments_status", "appointments", ["status"])
    op.execute(
        """
        ALTER TABLE appointments ADD CONSTRAINT ex_appointments_patient_no_overlap
        EXCLUDE USING gist (
            patient_id WITH =,
            tstzrange(scheduled_at, appointment_end(scheduled_at, duration_minutes), '[)') WITH &&
        ) WHERE (status IN ('scheduled', 'rescheduled'))
        """
    )

    # --- users ---
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "admin", "patient", name="user_role_enum", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patients.id", ondelete="CASCADE"),
            unique=True,
        ),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(role = 'patient' AND patient_id IS NOT NULL) "
            "OR (role = 'admin' AND patient_id IS NULL)",
            name="ck_users_role_patient_link",
        ),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    # --- auth_sessions ---
    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    # --- chat_pending_actions ---
    op.create_table(
        "chat_pending_actions",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "kind",
            sa.Enum(
                "schedule",
                "cancel",
                "reschedule",
                name="pending_action_kind_enum",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_pending_actions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")

    op.execute("ALTER TABLE appointments DROP CONSTRAINT ex_appointments_patient_no_overlap")
    op.drop_index("ix_appointments_status", table_name="appointments")
    op.drop_index("ix_appointments_scheduled_at", table_name="appointments")
    op.drop_index("ix_appointments_patient_id", table_name="appointments")
    op.drop_constraint("appointments_patient_id_fkey", "appointments", type_="foreignkey")
    op.create_foreign_key(
        "appointments_patient_id_fkey", "appointments", "patients", ["patient_id"], ["id"]
    )
    op.drop_constraint("ck_appointments_duration_range", "appointments", type_="check")
    op.drop_constraint("appointment_status_enum", "appointments", type_="check")
    op.drop_constraint("appointment_type_enum", "appointments", type_="check")
    op.alter_column("appointments", "created_at", nullable=True)
    op.alter_column("appointments", "status", nullable=True)
    op.alter_column("appointments", "duration_minutes", nullable=True)

    op.drop_constraint("ck_patients_email_format", "patients", type_="check")
    op.drop_constraint("ck_patients_name_not_blank", "patients", type_="check")
    op.drop_index("uq_patients_email_lower", table_name="patients")
    op.alter_column("patients", "created_at", nullable=True)

    op.execute("DROP FUNCTION IF EXISTS appointment_end(timestamptz, integer)")
