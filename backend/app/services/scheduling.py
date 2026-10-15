"""
Scheduling domain logic shared by the REST API, the chat agent tools, and the MCP server.

Every write path goes through this module so that validation (clinic hours, future-only,
overlap detection, allowed status transitions) is identical no matter who calls it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    ACTIVE_STATUSES,
    Appointment,
    AppointmentStatus,
    AppointmentType,
    Patient,
)

MIN_DURATION = 5
MAX_DURATION = 240
MAX_DAYS_AHEAD = 365

TYPE_LABELS = {
    AppointmentType.checkup: "Checkup",
    AppointmentType.follow_up: "Follow-up",
    AppointmentType.sick_visit: "Sick visit",
    AppointmentType.physical: "Physical",
    AppointmentType.vaccination: "Vaccination",
    AppointmentType.new_patient: "New patient visit",
}

_TYPE_SYNONYMS: list[tuple[str, AppointmentType]] = [
    ("new patient", AppointmentType.new_patient),
    ("first visit", AppointmentType.new_patient),
    ("follow up", AppointmentType.follow_up),
    ("followup", AppointmentType.follow_up),
    ("sick", AppointmentType.sick_visit),
    ("not feeling well", AppointmentType.sick_visit),
    ("ill", AppointmentType.sick_visit),
    ("cold", AppointmentType.sick_visit),
    ("cough", AppointmentType.sick_visit),
    ("fever", AppointmentType.sick_visit),
    ("flu shot", AppointmentType.vaccination),
    ("vaccin", AppointmentType.vaccination),
    ("immuniz", AppointmentType.vaccination),
    ("shot", AppointmentType.vaccination),
    ("physical", AppointmentType.physical),
    ("annual exam", AppointmentType.physical),
    ("check up", AppointmentType.checkup),
    ("checkup", AppointmentType.checkup),
    ("well visit", AppointmentType.checkup),
    ("well child", AppointmentType.checkup),
    ("wellness", AppointmentType.checkup),
]

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class SchedulingError(Exception):
    """A user-facing validation or lookup failure."""

    def __init__(self, message: str, *, candidates: list[dict] | None = None):
        super().__init__(message)
        self.message = message
        self.candidates = candidates or []

    def to_dict(self) -> dict:
        data: dict = {"error": self.message}
        if self.candidates:
            data["candidates"] = self.candidates
        return data


# --- Time helpers ---


def clinic_tz() -> ZoneInfo:
    return ZoneInfo(settings.clinic_timezone)


def clinic_now() -> datetime:
    return datetime.now(clinic_tz())


def format_local(dt: datetime) -> str:
    local = dt.astimezone(clinic_tz())
    hour = local.strftime("%I").lstrip("0")
    day = f"{local.strftime('%A, %B')} {local.day}, {local.year}"
    return f"{day} at {hour}:{local.strftime('%M %p %Z')}"


def clinic_hours_description() -> str:
    days = [_WEEKDAYS[d].capitalize() for d in sorted(settings.clinic_open_weekdays)]
    day_text = f"{days[0]}–{days[-1]}" if len(days) > 1 else days[0]
    open_t = time(settings.clinic_open_hour).strftime("%I:%M %p").lstrip("0")
    close_t = time(settings.clinic_close_hour).strftime("%I:%M %p").lstrip("0")
    return f"{day_text}, {open_t} to {close_t} ({settings.clinic_timezone})"


def _parse_date_text(text: str, today: date) -> date:
    raw = text.strip().lower().rstrip(".")
    raw = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", raw)
    # Drop a leading weekday name when a concrete date follows ("Friday 2026-10-16").
    raw = re.sub(r"^(" + "|".join(_WEEKDAYS) + r"),?\s+(?=\d|[a-z]{3})", "", raw)
    if raw in ("today",):
        return today
    if raw in ("tomorrow", "tmrw"):
        return today + timedelta(days=1)
    if raw in ("day after tomorrow",):
        return today + timedelta(days=2)
    m = re.fullmatch(r"(?:(this|next|coming|on)\s+)?(" + "|".join(_WEEKDAYS) + r")", raw)
    if m:
        # "Tuesday", "this Tuesday" and "next Tuesday" all mean the next upcoming Tuesday;
        # the confirmation step always shows the resolved calendar date so it can be corrected.
        target = _WEEKDAYS.index(m.group(2))
        days_ahead = (target - today.weekday()) % 7 or 7
        return today + timedelta(days=days_ahead)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", raw)
    if m:
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        parsed = date(year, int(m.group(1)), int(m.group(2)))
        if not m.group(3) and parsed < today:
            parsed = parsed.replace(year=today.year + 1)
        return parsed
    cleaned = re.sub(r"^(" + "|".join(_WEEKDAYS) + r"),?\s+", "", raw)
    for fmt in (
        "%B %d, %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%b %d %Y",
        "%B %d",
        "%b %d",
        "%d %B",
        "%d %b",
    ):
        try:
            parsed = datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
        if "%Y" not in fmt:
            parsed = parsed.replace(year=today.year)
            if parsed < today:
                parsed = parsed.replace(year=today.year + 1)
        return parsed
    try:
        import dateparser

        parsed_dt = dateparser.parse(
            text,
            settings={
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": datetime.combine(today, time(0)),
            },
        )
    except Exception:  # pragma: no cover - dateparser is optional at runtime
        parsed_dt = None
    if parsed_dt is None:
        raise SchedulingError(
            f"I couldn't understand the date '{text}'. Please give a date like 2026-10-20 or "
            "'next Tuesday'."
        )
    return parsed_dt.date()


def _parse_time_text(text: str) -> time:
    raw = text.strip().lower().replace(".", "")
    if raw in ("noon", "midday"):
        return time(12, 0)
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(am|pm|a|p)?", raw)
    if not m:
        raise SchedulingError(
            f"I couldn't understand the time '{text}'. Please give a time like 10:30 AM or 14:00."
        )
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = m.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            raise SchedulingError(f"'{text}' is not a valid time.")
        if meridiem.startswith("p") and hour != 12:
            hour += 12
        if meridiem.startswith("a") and hour == 12:
            hour = 0
    elif 1 <= hour <= 6:
        # Clinic hours never start before 8 AM, so "at 3" means 3 PM.
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SchedulingError(f"'{text}' is not a valid time.")
    return time(hour, minute)


def parse_clinic_datetime(date_text: str, time_text: str | None = None) -> datetime:
    """Interpret a date (and optional time) as clinic wall-clock time.

    Any UTC offset in the input is deliberately ignored: patients and staff speak in clinic
    local time, and language models often append a spurious 'Z'.
    """
    date_text = (date_text or "").strip()
    time_text = (time_text or "").strip()
    if not date_text:
        raise SchedulingError("A date is required.")

    if not time_text:
        iso = re.fullmatch(
            r"(\d{4}-\d{2}-\d{2})[T ](\d{1,2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?",
            date_text,
        )
        if iso:
            date_text, time_text = iso.group(1), iso.group(2)
        else:
            m = re.match(
                r"^(.*?)(?:\s+at\s+|,\s*|\s+)(\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)|noon)$",
                date_text,
                flags=re.IGNORECASE,
            )
            if m:
                date_text, time_text = m.group(1), m.group(2)
    if not time_text:
        raise SchedulingError("A time is required (for example 10:30 AM).")

    day = _parse_date_text(date_text, clinic_now().date())
    return datetime.combine(day, _parse_time_text(time_text), tzinfo=clinic_tz())


def parse_clinic_date(text: str) -> date:
    """Parse a day reference; any time-of-day in the text is ignored."""
    try:
        return parse_clinic_datetime(text).date()
    except SchedulingError:
        return _parse_date_text(text, clinic_now().date())


def ensure_aware(dt: datetime) -> datetime:
    """REST/MCP callers may send naive datetimes; treat them as clinic local time."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=clinic_tz())
    return dt


def validate_appointment_window(start: datetime, duration_minutes: int) -> None:
    if not MIN_DURATION <= duration_minutes <= MAX_DURATION:
        raise SchedulingError(
            f"Duration must be between {MIN_DURATION} and {MAX_DURATION} minutes."
        )
    start = ensure_aware(start)
    now = clinic_now()
    if start <= now:
        raise SchedulingError(
            f"{format_local(start)} is in the past. Please choose a future date and time."
        )
    if start > now + timedelta(days=MAX_DAYS_AHEAD):
        raise SchedulingError("Appointments can only be booked up to one year in advance.")
    local_start = start.astimezone(clinic_tz())
    local_end = local_start + timedelta(minutes=duration_minutes)
    if local_start.weekday() not in settings.clinic_open_weekdays:
        raise SchedulingError(
            f"The clinic is closed on {local_start.strftime('%A')}s. "
            f"Clinic hours are {clinic_hours_description()}."
        )
    opens = local_start.replace(hour=settings.clinic_open_hour, minute=0, second=0, microsecond=0)
    closes = local_start.replace(hour=settings.clinic_close_hour, minute=0, second=0, microsecond=0)
    if local_start < opens or local_end > closes:
        raise SchedulingError(
            f"{format_local(start)} is outside clinic hours. "
            f"Clinic hours are {clinic_hours_description()}."
        )


# --- Normalization ---


def normalize_appointment_type(value: str | AppointmentType | None) -> AppointmentType:
    if isinstance(value, AppointmentType):
        return value
    raw = (value or "").strip().lower()
    if not raw:
        raise SchedulingError(
            "Please say what kind of visit this is: "
            + ", ".join(t.value for t in AppointmentType)
            + "."
        )
    key = raw.replace("-", "_").replace(" ", "_")
    try:
        return AppointmentType(key)
    except ValueError:
        pass
    spaced = raw.replace("-", " ").replace("_", " ")
    for needle, appointment_type in _TYPE_SYNONYMS:
        if re.search(rf"\b{re.escape(needle)}", spaced):
            return appointment_type
    raise SchedulingError(
        f"'{value}' is not a visit type we book. Choose one of: "
        + ", ".join(t.value for t in AppointmentType)
        + "."
    )


def parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    match = _UUID_RE.search(value or "")
    return uuid.UUID(match.group(0)) if match else None


# --- Serialization ---


def patient_to_dict(patient: Patient, *, detailed: bool = False) -> dict:
    data = {
        "id": str(patient.id),
        "name": patient.name,
        "email": patient.email,
        "phone": patient.phone,
    }
    if detailed:
        data.update(
            {
                "date_of_birth": patient.date_of_birth.isoformat()
                if patient.date_of_birth
                else None,
                "notes": patient.notes,
                "registered_at": patient.created_at.isoformat() if patient.created_at else None,
            }
        )
    return data


def appointment_to_dict(appointment: Appointment, patient: Patient | None = None) -> dict:
    patient = patient or appointment.patient
    return {
        "id": str(appointment.id),
        "patient_id": str(appointment.patient_id),
        "patient_name": patient.name if patient else None,
        "appointment_type": appointment.appointment_type.value,
        "scheduled_at": appointment.scheduled_at.isoformat(),
        "when": format_local(appointment.scheduled_at),
        "duration_minutes": appointment.duration_minutes,
        "status": appointment.status.value,
        "reason": appointment.reason,
        "notes": appointment.notes,
    }


# --- Patient lookup ---


async def search_patients(db: AsyncSession, query: str | None = None) -> list[Patient]:
    """Case-insensitive search on name or email. Multi-word queries also match when every word
    appears in the name in any order (e.g. "Garcia, Maria")."""
    stmt = select(Patient).order_by(Patient.name)
    query = (query or "").strip()
    if not query:
        return list((await db.execute(stmt)).scalars().all())

    conditions = [Patient.name.ilike(f"%{query}%"), Patient.email.ilike(f"%{query}%")]
    terms = [t.strip(",.") for t in query.split() if t.strip(",.")]
    if len(terms) > 1:
        conditions.append(and_(*[Patient.name.ilike(f"%{t}%") for t in terms]))
    return list((await db.execute(stmt.where(or_(*conditions)))).scalars().all())


async def resolve_patient(db: AsyncSession, reference: str | uuid.UUID) -> Patient:
    """Resolve a patient UUID, exact name, or unambiguous partial name/email to one patient."""
    pid = parse_uuid(reference) if not isinstance(reference, uuid.UUID) else reference
    if pid:
        patient = await db.get(Patient, pid)
        if patient:
            return patient
        raise SchedulingError(f"No patient exists with id {pid}.")

    ref = str(reference or "").strip()
    if not ref:
        raise SchedulingError("Please tell me which patient this is for.")

    exact = (
        (
            await db.execute(
                select(Patient).where(
                    or_(
                        func.lower(Patient.name) == ref.lower(),
                        func.lower(Patient.email) == ref.lower(),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    if len(exact) == 1:
        return exact[0]

    matches = exact or await search_patients(db, ref)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SchedulingError(
            f"No patient matches '{ref}'. Check the spelling, or add them as a new patient first."
        )
    raise SchedulingError(
        f"More than one patient matches '{ref}'. Which one did you mean?",
        candidates=[patient_to_dict(p) for p in matches],
    )


# --- Appointment queries ---


async def list_appointments(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID | None = None,
    status: AppointmentStatus | str | None = None,
    from_date: datetime | None = None,
    upcoming_only: bool = False,
) -> list[tuple[Appointment, Patient]]:
    stmt = (
        select(Appointment, Patient)
        .join(Patient, Patient.id == Appointment.patient_id)
        .order_by(Appointment.scheduled_at)
    )
    if patient_id:
        stmt = stmt.where(Appointment.patient_id == patient_id)
    if status:
        try:
            stmt = stmt.where(Appointment.status == AppointmentStatus(status))
        except ValueError as e:
            raise SchedulingError(
                f"Unknown status '{status}'. Use one of: "
                + ", ".join(s.value for s in AppointmentStatus)
            ) from e
    if from_date:
        stmt = stmt.where(Appointment.scheduled_at >= ensure_aware(from_date))
    if upcoming_only:
        stmt = stmt.where(
            Appointment.scheduled_at > clinic_now(), Appointment.status.in_(ACTIVE_STATUSES)
        )
    return [(a, p) for a, p in (await db.execute(stmt)).all()]


async def find_active_appointment(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    appointment_id: uuid.UUID | None = None,
    appointment_type: AppointmentType | None = None,
    on_date: date | None = None,
) -> Appointment:
    """Find exactly one upcoming, active appointment for a patient, or explain why not."""
    stmt = (
        select(Appointment)
        .where(
            Appointment.patient_id == patient_id,
            Appointment.status.in_(ACTIVE_STATUSES),
            Appointment.scheduled_at > clinic_now(),
        )
        .order_by(Appointment.scheduled_at)
    )
    if appointment_id:
        stmt = stmt.where(Appointment.id == appointment_id)
    if appointment_type:
        stmt = stmt.where(Appointment.appointment_type == appointment_type)
    matches = list((await db.execute(stmt)).scalars().all())
    if on_date:
        matches = [a for a in matches if a.scheduled_at.astimezone(clinic_tz()).date() == on_date]

    if len(matches) == 1:
        return matches[0]
    if not matches:
        patient = await db.get(Patient, patient_id)
        what = TYPE_LABELS[appointment_type].lower() if appointment_type else "appointment"
        on = f" on {on_date:%A, %B} {on_date.day}" if on_date else ""
        raise SchedulingError(
            f"{patient.name if patient else 'This patient'} has no upcoming {what}{on}."
        )
    raise SchedulingError(
        "More than one upcoming appointment matches. Which one do you mean?",
        candidates=[
            {
                "id": str(a.id),
                "appointment_type": a.appointment_type.value,
                "when": format_local(a.scheduled_at),
            }
            for a in matches
        ],
    )


async def _check_conflict(
    db: AsyncSession,
    patient_id: uuid.UUID,
    start: datetime,
    duration_minutes: int,
    exclude_id: uuid.UUID | None = None,
) -> None:
    end = start + timedelta(minutes=duration_minutes)
    stmt = select(Appointment).where(
        Appointment.patient_id == patient_id,
        Appointment.status.in_(ACTIVE_STATUSES),
        Appointment.scheduled_at < end,
        func.appointment_end(Appointment.scheduled_at, Appointment.duration_minutes) > start,
    )
    if exclude_id:
        stmt = stmt.where(Appointment.id != exclude_id)
    clash = (await db.execute(stmt.limit(1))).scalar_one_or_none()
    if clash:
        raise SchedulingError(
            "That time overlaps this patient's existing "
            f"{TYPE_LABELS[clash.appointment_type].lower()} on {format_local(clash.scheduled_at)}."
        )


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        if "ex_appointments_patient_no_overlap" in str(e.orig):
            raise SchedulingError(
                "That time overlaps another active appointment for this patient."
            ) from e
        raise SchedulingError("The change violates a data-integrity rule and was not saved.") from e


# --- Writes ---


@dataclass
class ScheduleRequest:
    patient_id: uuid.UUID
    appointment_type: AppointmentType
    scheduled_at: datetime
    duration_minutes: int = 30
    reason: str | None = None
    notes: str | None = None


async def validate_new_appointment(db: AsyncSession, req: ScheduleRequest) -> Patient:
    patient = await db.get(Patient, req.patient_id)
    if not patient:
        raise SchedulingError("Patient not found.")
    validate_appointment_window(req.scheduled_at, req.duration_minutes)
    await _check_conflict(db, patient.id, ensure_aware(req.scheduled_at), req.duration_minutes)
    return patient


async def create_appointment(db: AsyncSession, req: ScheduleRequest) -> Appointment:
    patient = await validate_new_appointment(db, req)
    appointment = Appointment(
        patient_id=patient.id,
        appointment_type=req.appointment_type,
        scheduled_at=ensure_aware(req.scheduled_at),
        duration_minutes=req.duration_minutes,
        status=AppointmentStatus.scheduled,
        reason=(req.reason or "").strip() or None,
        notes=(req.notes or "").strip() or None,
    )
    db.add(appointment)
    await _commit(db)
    await db.refresh(appointment)
    return appointment


def _append_note(existing: str | None, note: str) -> str:
    return f"{existing or ''}\n{note}".strip()


async def validate_reschedule(
    db: AsyncSession, appointment: Appointment, new_start: datetime, duration_minutes: int | None
) -> int:
    if appointment.status not in ACTIVE_STATUSES:
        raise SchedulingError(
            f"This appointment is {appointment.status.value} and cannot be rescheduled."
        )
    duration = duration_minutes or appointment.duration_minutes
    validate_appointment_window(new_start, duration)
    if (
        ensure_aware(new_start) == appointment.scheduled_at
        and duration == appointment.duration_minutes
    ):
        raise SchedulingError("The appointment is already at that time.")
    await _check_conflict(
        db, appointment.patient_id, ensure_aware(new_start), duration, exclude_id=appointment.id
    )
    return duration


async def reschedule_appointment(
    db: AsyncSession,
    appointment: Appointment,
    new_start: datetime,
    *,
    duration_minutes: int | None = None,
    note: str | None = None,
) -> datetime:
    """Move an appointment; returns the previous start time."""
    duration = await validate_reschedule(db, appointment, new_start, duration_minutes)
    previous = appointment.scheduled_at
    appointment.scheduled_at = ensure_aware(new_start)
    appointment.duration_minutes = duration
    appointment.status = AppointmentStatus.rescheduled
    appointment.notes = _append_note(
        appointment.notes,
        f"Rescheduled from {format_local(previous)}" + (f": {note.strip()}" if note else "."),
    )
    await _commit(db)
    await db.refresh(appointment)
    return previous


async def cancel_appointment(
    db: AsyncSession, appointment: Appointment, *, reason: str | None = None
) -> Appointment:
    if appointment.status not in ACTIVE_STATUSES:
        raise SchedulingError(f"This appointment is already {appointment.status.value}.")
    appointment.status = AppointmentStatus.cancelled
    appointment.notes = _append_note(
        appointment.notes, "Cancelled" + (f": {reason.strip()}" if reason else ".")
    )
    await _commit(db)
    await db.refresh(appointment)
    return appointment


_ALLOWED_TRANSITIONS: dict[AppointmentStatus, set[AppointmentStatus]] = {
    AppointmentStatus.scheduled: {
        AppointmentStatus.rescheduled,
        AppointmentStatus.completed,
        AppointmentStatus.cancelled,
    },
    AppointmentStatus.rescheduled: {AppointmentStatus.completed, AppointmentStatus.cancelled},
    AppointmentStatus.completed: set(),
    AppointmentStatus.cancelled: {AppointmentStatus.scheduled},
}


async def update_appointment(
    db: AsyncSession, appointment: Appointment, changes: dict
) -> Appointment:
    """Generic admin update with status-transition and calendar validation."""
    new_status = changes.pop("status", None)
    if new_status is not None:
        new_status = AppointmentStatus(new_status)
        if (
            new_status != appointment.status
            and new_status not in _ALLOWED_TRANSITIONS[appointment.status]
        ):
            raise SchedulingError(
                f"Cannot change an appointment from {appointment.status.value} "
                f"to {new_status.value}."
            )

    new_start = changes.get("scheduled_at")
    new_duration = changes.get("duration_minutes")
    if new_start is not None:
        changes["scheduled_at"] = new_start = ensure_aware(new_start)
    time_changed = (new_start is not None and new_start != appointment.scheduled_at) or (
        new_duration is not None and new_duration != appointment.duration_minutes
    )
    final_status = new_status or appointment.status
    becomes_active = final_status in ACTIVE_STATUSES and (appointment.status not in ACTIVE_STATUSES)

    if appointment.status == AppointmentStatus.completed and (
        time_changed or "appointment_type" in changes
    ):
        raise SchedulingError("Completed appointments cannot be changed.")
    if (time_changed and final_status in ACTIVE_STATUSES) or becomes_active:
        start = new_start or appointment.scheduled_at
        duration = new_duration or appointment.duration_minutes
        validate_appointment_window(start, duration)
        await _check_conflict(
            db, appointment.patient_id, start, duration, exclude_id=appointment.id
        )
        if (
            time_changed
            and new_status is None
            and appointment.status == AppointmentStatus.scheduled
        ):
            final_status = AppointmentStatus.rescheduled

    for field, value in changes.items():
        setattr(appointment, field, value)
    appointment.status = final_status
    await _commit(db)
    await db.refresh(appointment)
    return appointment
