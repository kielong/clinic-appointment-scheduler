"""
Server-side confirmation gate for chat writes.

Write tools never touch appointments directly. They validate the request and store a
`ChatPendingAction`. The chat route executes it only when the user's next message is an
explicit confirmation, so the language model can neither skip confirmation nor alter what
was confirmed.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Appointment,
    AppointmentType,
    ChatPendingAction,
    Patient,
    PendingActionKind,
    User,
    UserRole,
)
from app.services import scheduling
from app.services.scheduling import SchedulingError

PENDING_TTL = timedelta(minutes=15)

_CONFIRM_RE = re.compile(
    r"^\s*(y|yes|yeah|yep|yup|sure|ok|okay|confirm|confirmed|correct|go ahead|do it|book it|"
    r"sounds good|looks good|that's right|that is right)"
    r"(\s*(,\s*)?(please|thanks|thank you|confirm|book it|go ahead|do it))*\s*[.!]*\s*$",
    re.IGNORECASE,
)
_REJECT_RE = re.compile(
    r"^\s*(n|no|nope|nah|cancel|cancel that|stop|don't|do not|never ?mind|forget it|wait)"
    r"(\s*(,\s*)?(thanks|thank you|please))*\s*[.!]*\s*$",
    re.IGNORECASE,
)


def is_confirmation(text: str) -> bool:
    return bool(_CONFIRM_RE.match(text or ""))


def is_rejection(text: str) -> bool:
    return bool(_REJECT_RE.match(text or ""))


async def propose(
    db: AsyncSession, user_id: uuid.UUID, kind: PendingActionKind, payload: dict, summary: str
) -> None:
    """Store (or replace) the user's single pending action."""
    now = datetime.now(UTC)
    stmt = insert(ChatPendingAction).values(
        user_id=user_id,
        kind=kind,
        payload=payload,
        summary=summary,
        created_at=now,
        expires_at=now + PENDING_TTL,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ChatPendingAction.user_id],
        set_={
            "kind": stmt.excluded.kind,
            "payload": stmt.excluded.payload,
            "summary": stmt.excluded.summary,
            "created_at": stmt.excluded.created_at,
            "expires_at": stmt.excluded.expires_at,
        },
    )
    await db.execute(stmt)
    await db.commit()


async def get_pending(db: AsyncSession, user_id: uuid.UUID) -> ChatPendingAction | None:
    action = await db.get(ChatPendingAction, user_id)
    if action and action.expires_at <= datetime.now(UTC):
        await clear(db, user_id)
        return None
    return action


async def clear(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(delete(ChatPendingAction).where(ChatPendingAction.user_id == user_id))
    await db.commit()


async def _load_owned_appointment(
    db: AsyncSession, user: User, appointment_id: str
) -> tuple[Appointment, Patient]:
    appointment = await db.get(Appointment, uuid.UUID(appointment_id))
    if not appointment:
        raise SchedulingError("That appointment no longer exists.")
    if user.role == UserRole.patient and appointment.patient_id != user.patient_id:
        raise SchedulingError("You can only change your own appointments.")
    patient = await db.get(Patient, appointment.patient_id)
    return appointment, patient


async def execute(db: AsyncSession, user: User, action: ChatPendingAction) -> str:
    """Run a confirmed action and return the message to show the user.

    Everything is re-validated here because time has passed since the proposal (the slot may
    have been taken, or the appointment cancelled from the dashboard).
    """
    payload = action.payload
    kind = PendingActionKind(action.kind)
    await clear(db, user.id)

    if kind == PendingActionKind.schedule:
        patient_id = uuid.UUID(payload["patient_id"])
        if user.role == UserRole.patient and patient_id != user.patient_id:
            raise SchedulingError("You can only book appointments for yourself.")
        appointment = await scheduling.create_appointment(
            db,
            scheduling.ScheduleRequest(
                patient_id=patient_id,
                appointment_type=AppointmentType(payload["appointment_type"]),
                scheduled_at=datetime.fromisoformat(payload["scheduled_at"]),
                duration_minutes=int(payload["duration_minutes"]),
                reason=payload.get("reason"),
            ),
        )
        patient = await db.get(Patient, appointment.patient_id)
        label = scheduling.TYPE_LABELS[appointment.appointment_type]
        return (
            f"Done — {label.lower()} booked for {patient.name} on "
            f"{scheduling.format_local(appointment.scheduled_at)} "
            f"({appointment.duration_minutes} min). Confirmation #{str(appointment.id)[:8]}."
        )

    appointment, patient = await _load_owned_appointment(db, user, payload["appointment_id"])
    label = scheduling.TYPE_LABELS[appointment.appointment_type]

    if kind == PendingActionKind.cancel:
        when = scheduling.format_local(appointment.scheduled_at)
        await scheduling.cancel_appointment(db, appointment, reason=payload.get("reason"))
        return f"Done — {patient.name}'s {label.lower()} on {when} has been cancelled."

    previous = await scheduling.reschedule_appointment(
        db,
        appointment,
        datetime.fromisoformat(payload["new_scheduled_at"]),
        note=payload.get("note"),
    )
    return (
        f"Done — {patient.name}'s {label.lower()} has been moved from "
        f"{scheduling.format_local(previous)} to "
        f"{scheduling.format_local(appointment.scheduled_at)}."
    )
