"""
LangChain tools for the clinic scheduling agent.

Tools are built per request and closed over the signed-in user:

* Patients get tools that are hard-wired to their own patient record. The model never
  supplies a patient id, so it cannot read or change anyone else's data.
* Admins get tools that can look up and act on any patient.

Write tools (schedule / cancel / reschedule) only *propose* a change: they validate it and
store a pending action. The chat route executes it after the user explicitly confirms.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime, time

from langchain_core.tools import BaseTool, tool
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import pending
from app.database import async_session
from app.models import (
    AppointmentStatus,
    AppointmentType,
    Patient,
    PendingActionKind,
    User,
    UserRole,
)
from app.services import scheduling
from app.services.scheduling import SchedulingError

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

AWAITING_CONFIRMATION = "awaiting_confirmation"
_EMPTY = {"", "none", "null", "n/a", "na", "any", "unknown", "undefined"}


def _clean(value: object) -> str:
    """Small models often send 'None' / 'null' for optional args; treat those as empty."""
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in _EMPTY else text


def _dump(data: dict) -> str:
    output = json.dumps(data, default=str)
    logger.info("[tool.output] %s", output[:1200])
    return output


def _proposal(summary: str) -> str:
    return _dump({"status": AWAITING_CONFIRMATION, "summary": summary})


def _schedule_summary(
    patient: Patient, appointment_type, start: datetime, duration: int, reason: str | None
) -> str:
    lines = [
        "Please confirm this booking:",
        f"• Patient: {patient.name}",
        f"• Visit: {scheduling.TYPE_LABELS[appointment_type]}",
        f"• When: {scheduling.format_local(start)}",
        f"• Duration: {duration} min",
    ]
    if reason:
        lines.append(f"• Reason: {reason}")
    lines.append('Reply "yes" to confirm or "no" to cancel.')
    return "\n".join(lines)


def _optional_date(text: str):
    return scheduling.parse_clinic_date(text) if text else None


# --- Grounding: check the model's date/time arguments against what the user actually typed ---

_TIME_IN_TEXT = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?|\b(\d{1,2}):(\d{2})\b|\b(noon)\b",
    re.IGNORECASE,
)
_WEEKDAY_IN_TEXT = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE
)


def _times_in(text: str) -> list[time]:
    found = []
    for m in _TIME_IN_TEXT.finditer(text or ""):
        if m.group(6):
            found.append(time(12, 0))
        elif m.group(3):
            hour, minute = int(m.group(1)), int(m.group(2) or 0)
            if 1 <= hour <= 12 and minute < 60:
                hour = hour % 12 + (12 if m.group(3).lower() == "p" else 0)
                found.append(time(hour, minute))
        else:
            hour, minute = int(m.group(4)), int(m.group(5))
            if hour < 24 and minute < 60:
                if 1 <= hour <= 6:
                    hour += 12  # same "at 3:00 means 3 PM" rule as the time parser
                found.append(time(hour, minute))
    return found


_RESCHEDULE_INTENT = re.compile(
    r"\b(re-?schedul\w*|move|moving|push\w* (?:it |this |that )?back|bump)\b", re.IGNORECASE
)


@dataclass
class Grounding:
    """The user's own words this turn, used to catch model slips (e.g. a hallucinated time)."""

    latest: str = ""
    earlier: list[str] = field(default_factory=list)

    def _mentions(self, extract) -> list:
        latest = list(dict.fromkeys(extract(self.latest)))
        if latest:
            return latest
        return list(dict.fromkeys(v for msg in self.earlier for v in extract(msg)))

    def time(self, model_time: str) -> str:
        stated = self._mentions(_times_in)
        if len(stated) != 1:
            return model_time
        try:
            if scheduling.parse_clinic_datetime("today", model_time).time() == stated[0]:
                return model_time
        except SchedulingError:
            pass
        logger.info("[grounding] time %r -> user said %s", model_time, stated[0])
        return stated[0].strftime("%H:%M")

    def wants_reschedule(self) -> bool:
        return bool(_RESCHEDULE_INTENT.search(self.latest))

    async def patient(self, db: AsyncSession, model_patient: str) -> str:
        """If the model omitted the patient, use the one patient the user's message names."""
        if model_patient:
            return model_patient
        text = self.latest.lower()
        named = [p for p in await scheduling.search_patients(db) if p.name.lower() in text]
        if not named:
            first_names: dict[str, list[Patient]] = {}
            for p in await scheduling.search_patients(db):
                first_names.setdefault(p.name.split()[0].lower(), []).append(p)
            named = [
                ps[0]
                for first, ps in first_names.items()
                if len(ps) == 1 and re.search(rf"\b{re.escape(first)}\b", text)
            ]
        if len(named) == 1:
            logger.info("[grounding] patient '' -> %s", named[0].name)
            return str(named[0].id)
        return model_patient

    def date(self, model_date: str) -> str:
        stated = self._mentions(lambda t: [w.lower() for w in _WEEKDAY_IN_TEXT.findall(t or "")])
        if len(stated) != 1 or not model_date:
            return model_date
        try:
            if scheduling.parse_clinic_date(model_date).strftime("%A").lower() == stated[0]:
                return model_date
        except SchedulingError:
            pass
        logger.info("[grounding] date %r -> user said %s", model_date, stated[0])
        return stated[0]


def build_tools(
    user: User,
    session_factory: SessionFactory = async_session,
    grounding: Grounding | None = None,
) -> list[BaseTool]:
    grounding = grounding or Grounding()
    if user.role == UserRole.admin:
        return _admin_tools(user, session_factory, grounding)
    return _patient_tools(user, session_factory, grounding)


# --- Shared proposal logic ---


async def _propose_schedule(
    db: AsyncSession,
    user: User,
    patient: Patient,
    appointment_type: str,
    date: str,
    time: str,
    duration_minutes: int | str | None,
    reason: str,
    grounding: Grounding,
) -> str:
    appt_type = scheduling.normalize_appointment_type(_clean(appointment_type) or reason)
    start = scheduling.parse_clinic_datetime(
        grounding.date(_clean(date)), grounding.time(_clean(time))
    )
    try:
        duration = int(_clean(duration_minutes) or 30)
    except ValueError as e:
        raise SchedulingError("Duration must be a whole number of minutes.") from e
    reason = _clean(reason) or None
    if grounding.wants_reschedule():
        # The user asked to move an existing visit but the model reached for the booking tool.
        # If they have no such visit, fall through and book (e.g. "I'm moving here next month").
        existing = [
            a
            for a, _ in await scheduling.list_appointments(
                db, patient_id=patient.id, upcoming_only=True
            )
            if a.appointment_type == appt_type
        ]
        if len(existing) > 1:
            existing = [
                await scheduling.find_active_appointment(
                    db, patient_id=patient.id, appointment_type=appt_type
                )
            ]  # raises with the candidate list
    else:
        existing = []
    if existing:
        return await _propose_reschedule(
            db, user, patient, date, time, str(existing[0].id), "", "", grounding
        )
    await scheduling.validate_new_appointment(
        db,
        scheduling.ScheduleRequest(
            patient_id=patient.id,
            appointment_type=appt_type,
            scheduled_at=start,
            duration_minutes=duration,
            reason=reason,
        ),
    )
    summary = _schedule_summary(patient, appt_type, start, duration, reason)
    same_type = await scheduling.list_appointments(db, patient_id=patient.id, upcoming_only=True)
    same_type = [a for a, _ in same_type if a.appointment_type == appt_type]
    if same_type:
        label = scheduling.TYPE_LABELS[appt_type].lower()
        when = scheduling.format_local(same_type[0].scheduled_at)
        summary = summary.replace(
            "\nReply",
            f"\nNote: {patient.name} already has a {label} on {when}. "
            "This books an additional visit.\nReply",
        )
    await pending.propose(
        db,
        user.id,
        PendingActionKind.schedule,
        {
            "patient_id": str(patient.id),
            "appointment_type": appt_type.value,
            "scheduled_at": start.isoformat(),
            "duration_minutes": duration,
            "reason": reason,
        },
        summary,
    )
    return _proposal(summary)


async def _find_target(
    db: AsyncSession,
    patient: Patient,
    appointment_id: str,
    appointment_type: str,
    current_date: str,
):
    appt_type = _clean(appointment_type)
    return await scheduling.find_active_appointment(
        db,
        patient_id=patient.id,
        appointment_id=scheduling.parse_uuid(_clean(appointment_id)),
        appointment_type=scheduling.normalize_appointment_type(appt_type) if appt_type else None,
        on_date=_optional_date(_clean(current_date)),
    )


async def _propose_cancel(
    db: AsyncSession,
    user: User,
    patient: Patient,
    appointment_id: str,
    appointment_type: str,
    current_date: str,
    reason: str,
) -> str:
    appointment = await _find_target(db, patient, appointment_id, appointment_type, current_date)
    reason = _clean(reason) or None
    summary = "\n".join(
        [
            "Please confirm this cancellation:",
            f"• Patient: {patient.name}",
            f"• Visit: {scheduling.TYPE_LABELS[appointment.appointment_type]}",
            f"• When: {scheduling.format_local(appointment.scheduled_at)}",
            *([f"• Reason: {reason}"] if reason else []),
            'Reply "yes" to cancel this appointment or "no" to keep it.',
        ]
    )
    await pending.propose(
        db,
        user.id,
        PendingActionKind.cancel,
        {"appointment_id": str(appointment.id), "reason": reason},
        summary,
    )
    return _proposal(summary)


async def _propose_reschedule(
    db: AsyncSession,
    user: User,
    patient: Patient,
    new_date: str,
    new_time: str,
    appointment_id: str,
    appointment_type: str,
    current_date: str,
    grounding: Grounding,
) -> str:
    appointment = await _find_target(db, patient, appointment_id, appointment_type, current_date)
    new_start = scheduling.parse_clinic_datetime(
        grounding.date(_clean(new_date)), grounding.time(_clean(new_time))
    )
    await scheduling.validate_reschedule(db, appointment, new_start, None)
    summary = "\n".join(
        [
            "Please confirm this change:",
            f"• Patient: {patient.name}",
            f"• Visit: {scheduling.TYPE_LABELS[appointment.appointment_type]}",
            f"• From: {scheduling.format_local(appointment.scheduled_at)}",
            f"• To: {scheduling.format_local(new_start)}",
            'Reply "yes" to confirm or "no" to keep the original time.',
        ]
    )
    await pending.propose(
        db,
        user.id,
        PendingActionKind.reschedule,
        {"appointment_id": str(appointment.id), "new_scheduled_at": new_start.isoformat()},
        summary,
    )
    return _proposal(summary)


def _appointment_rows(rows) -> list[dict]:
    return [
        {
            "id": d["id"],
            "patient_name": d["patient_name"],
            "visit": scheduling.TYPE_LABELS[AppointmentType(d["appointment_type"])],
            "when": d["when"],
            "duration_minutes": d["duration_minutes"],
            "status": d["status"],
            "reason": d["reason"],
        }
        for d in (scheduling.appointment_to_dict(a, p) for a, p in rows)
    ]


_WRITE_TOOLS = {"schedule_appointment", "cancel_appointment", "reschedule_appointment"}


def _error_reply(e: SchedulingError) -> str:
    lines = [e.message]
    for c in e.candidates:
        if "when" in c:
            label = scheduling.TYPE_LABELS.get(AppointmentType(c["appointment_type"]), "")
            lines.append(f"• {label} on {c['when']}")
        else:
            lines.append(f"• {c['name']} ({c['email']})")
    return "\n".join(lines)


async def _run(name: str, fn) -> str:
    try:
        return await fn()
    except SchedulingError as e:
        data = e.to_dict()
        if name in _WRITE_TOOLS:
            # Relayed to the user verbatim by the chat route; small models garble these.
            data["reply"] = _error_reply(e)
        return _dump(data)


# --- Patient tools ---


def _patient_tools(
    user: User, session_factory: SessionFactory, grounding: Grounding
) -> list[BaseTool]:
    patient_id = user.patient_id

    async def _me(db: AsyncSession) -> Patient:
        patient = await db.get(Patient, patient_id)
        if not patient:
            raise SchedulingError("Your patient record could not be found. Please call the clinic.")
        return patient

    @tool(parse_docstring=True)
    async def my_appointments(include_past: bool = False) -> str:
        """List the signed-in patient's appointments.

        Args:
            include_past: Only true if the patient explicitly asks about past or cancelled visits.
        """
        async with session_factory() as db:
            rows = await scheduling.list_appointments(
                db, patient_id=patient_id, upcoming_only=not include_past
            )
            return _dump({"count": len(rows), "appointments": _appointment_rows(rows)})

    @tool(parse_docstring=True)
    async def schedule_appointment(
        appointment_type: str, date: str, time: str, reason: str = ""
    ) -> str:
        """Prepare a new appointment for the signed-in patient. The patient must confirm before
        it is booked; this tool shows them the confirmation.

        Args:
            appointment_type: checkup, follow_up, sick_visit, physical, vaccination, or new_patient.
            date: The day exactly as the user said it (a weekday, "tomorrow", or a date).
            time: The time exactly as the user said it.
            reason: Short reason for the visit, in the patient's words.
        """

        async def run():
            async with session_factory() as db:
                me = await _me(db)
                return await _propose_schedule(
                    db, user, me, appointment_type, date, time, None, reason, grounding
                )

        return await _run("schedule_appointment", run)

    @tool(parse_docstring=True)
    async def cancel_appointment(
        appointment_type: str = "", current_date: str = "", reason: str = ""
    ) -> str:
        """Prepare cancelling one of the signed-in patient's upcoming appointments. The patient
        must confirm before it is cancelled.

        Args:
            appointment_type: Type of the appointment to cancel, if the patient said it.
            current_date: Date of the appointment to cancel, if the patient said it.
            reason: Why it is being cancelled (optional).
        """

        async def run():
            async with session_factory() as db:
                me = await _me(db)
                return await _propose_cancel(
                    db, user, me, "", appointment_type, current_date, reason
                )

        return await _run("cancel_appointment", run)

    @tool(parse_docstring=True)
    async def reschedule_appointment(
        new_date: str, new_time: str, appointment_type: str = "", current_date: str = ""
    ) -> str:
        """Prepare moving one of the signed-in patient's upcoming appointments to a new time.
        The patient must confirm before it changes.

        Args:
            new_date: The new day exactly as the user said it.
            new_time: The new time exactly as the user said it.
            appointment_type: Type of the appointment to move, if the patient said it.
            current_date: Current date of the appointment to move, if the patient said it.
        """

        async def run():
            async with session_factory() as db:
                me = await _me(db)
                return await _propose_reschedule(
                    db, user, me, new_date, new_time, "", appointment_type, current_date, grounding
                )

        return await _run("reschedule_appointment", run)

    return [my_appointments, schedule_appointment, cancel_appointment, reschedule_appointment]


# --- Admin tools ---


def _admin_tools(
    user: User, session_factory: SessionFactory, grounding: Grounding
) -> list[BaseTool]:
    @tool(parse_docstring=True)
    async def search_patients(query: str = "") -> str:
        """Find patients by name or email. Leave query empty to list every patient.

        Args:
            query: Name or email fragment, e.g. "Maria" or "garcia@".
        """
        async with session_factory() as db:
            patients = await scheduling.search_patients(db, _clean(query))
            return _dump(
                {
                    "count": len(patients),
                    "patients": [scheduling.patient_to_dict(p) for p in patients],
                }
            )

    @tool(parse_docstring=True)
    async def get_patient_details(patient: str) -> str:
        """Get one patient's full profile and their upcoming appointments.

        Args:
            patient: The patient's full name, email, or id.
        """

        async def run():
            async with session_factory() as db:
                p = await scheduling.resolve_patient(
                    db, await grounding.patient(db, _clean(patient))
                )
                rows = await scheduling.list_appointments(db, patient_id=p.id, upcoming_only=True)
                return _dump(
                    {
                        "patient": scheduling.patient_to_dict(p, detailed=True),
                        "upcoming_appointments": _appointment_rows(rows),
                    }
                )

        return await _run("get_patient_details", run)

    @tool(parse_docstring=True)
    async def list_appointments(
        patient: str = "", status: str = "", upcoming_only: bool = True
    ) -> str:
        """List appointments, optionally for one patient or one status.

        Args:
            patient: Patient name, email, or id to filter by (optional).
            status: scheduled, rescheduled, completed, or cancelled (optional).
            upcoming_only: Only future scheduled/rescheduled visits. Set false for history.
        """

        async def run():
            async with session_factory() as db:
                patient_id = None
                patient_ref = await grounding.patient(db, _clean(patient))
                if patient_ref:
                    patient_id = (await scheduling.resolve_patient(db, patient_ref)).id
                status_value = _clean(status).lower() or None
                if status_value not in {s.value for s in AppointmentStatus}:
                    # e.g. "upcoming", "upcoming_only", "active", "future"
                    status_value = None
                    upcoming_only = True
                rows = await scheduling.list_appointments(
                    db,
                    patient_id=patient_id,
                    status=status_value,
                    upcoming_only=bool(upcoming_only) and status_value is None,
                )
                return _dump({"count": len(rows), "appointments": _appointment_rows(rows)})

        return await _run("list_appointments", run)

    @tool(parse_docstring=True)
    async def schedule_appointment(
        patient: str,
        appointment_type: str,
        date: str,
        time: str,
        duration_minutes: int = 30,
        reason: str = "",
    ) -> str:
        """Prepare a new appointment for a patient. Staff must confirm before it is booked;
        this tool shows them the confirmation.

        Args:
            patient: The patient's full name, email, or id.
            appointment_type: checkup, follow_up, sick_visit, physical, vaccination, or new_patient.
            date: The day exactly as the user said it (a weekday, "tomorrow", or a date).
            time: The time exactly as the user said it.
            duration_minutes: Length in minutes (default 30).
            reason: Short reason for the visit (optional).
        """

        async def run():
            async with session_factory() as db:
                p = await scheduling.resolve_patient(
                    db, await grounding.patient(db, _clean(patient))
                )
                return await _propose_schedule(
                    db, user, p, appointment_type, date, time, duration_minutes, reason, grounding
                )

        return await _run("schedule_appointment", run)

    @tool(parse_docstring=True)
    async def cancel_appointment(
        patient: str,
        appointment_type: str = "",
        current_date: str = "",
        appointment_id: str = "",
        reason: str = "",
    ) -> str:
        """Prepare cancelling a patient's upcoming appointment. Staff must confirm first.

        Args:
            patient: The patient's full name, email, or id.
            appointment_type: Type of the appointment to cancel, if known.
            current_date: Date of the appointment to cancel, if known.
            appointment_id: Appointment id, if known.
            reason: Why it is being cancelled (optional).
        """

        async def run():
            async with session_factory() as db:
                p = await scheduling.resolve_patient(
                    db, await grounding.patient(db, _clean(patient))
                )
                return await _propose_cancel(
                    db, user, p, appointment_id, appointment_type, current_date, reason
                )

        return await _run("cancel_appointment", run)

    @tool(parse_docstring=True)
    async def reschedule_appointment(
        patient: str,
        new_date: str,
        new_time: str,
        appointment_type: str = "",
        current_date: str = "",
        appointment_id: str = "",
    ) -> str:
        """Prepare moving a patient's upcoming appointment to a new time. Staff must confirm.

        Args:
            patient: The patient's full name, email, or id.
            new_date: The new day exactly as the user said it.
            new_time: The new time exactly as the user said it.
            appointment_type: Type of the appointment to move, if known.
            current_date: Current date of the appointment to move, if known.
            appointment_id: Appointment id, if known.
        """

        async def run():
            async with session_factory() as db:
                p = await scheduling.resolve_patient(
                    db, await grounding.patient(db, _clean(patient))
                )
                return await _propose_reschedule(
                    db,
                    user,
                    p,
                    new_date,
                    new_time,
                    appointment_id,
                    appointment_type,
                    current_date,
                    grounding,
                )

        return await _run("reschedule_appointment", run)

    return [
        search_patients,
        get_patient_details,
        list_appointments,
        schedule_appointment,
        cancel_appointment,
        reschedule_appointment,
    ]


def final_reply(tool_output: object) -> str | None:
    """Text to show the user verbatim (ending the turn) for write-tool results: either the
    confirmation summary of a proposal or the reason the request could not be prepared."""
    content = getattr(tool_output, "content", tool_output)
    try:
        data = json.loads(content) if isinstance(content, str) else None
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") == AWAITING_CONFIRMATION:
        return data.get("summary")
    return data.get("reply")
