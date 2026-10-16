"""
Seed the database with sample patients, appointments, and login accounts.

Run migrations first (`make migrate`); this script never creates or alters tables.
Every row has a fixed id or unique email and is inserted only if missing, so it is safe to run
repeatedly and never overwrites changes made in the app.

Usage: python -m app.seed
"""

import asyncio
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select

from app.config import settings
from app.database import async_session
from app.models import Appointment, AppointmentStatus, AppointmentType, Patient, User, UserRole
from app.security import hash_password
from app.services.scheduling import clinic_now, clinic_tz

SEED_PATIENTS = [
    dict(
        id=uuid.UUID("bbbb1111-0000-0000-0000-000000000001"),
        name="Maria Garcia",
        email="maria.garcia@example.com",
        phone="+1-555-555-0101",
        date_of_birth=date(1985, 3, 12),
        notes="Adult patient. History of seasonal allergies.",
    ),
    dict(
        id=uuid.UUID("bbbb1111-0000-0000-0000-000000000002"),
        name="James Wilson",
        email="james.wilson@example.com",
        phone="+1-555-555-0102",
        date_of_birth=date(1972, 8, 4),
        notes="Adult patient. Annual physical due.",
    ),
    dict(
        id=uuid.UUID("bbbb1111-0000-0000-0000-000000000003"),
        name="Sofia Martinez",
        email="sofia.martinez@example.com",
        phone="+1-555-555-0103",
        date_of_birth=date(2018, 11, 22),
        notes="Pediatric patient. Last well-child visit 6 months ago.",
    ),
    dict(
        id=uuid.UUID("bbbb1111-0000-0000-0000-000000000004"),
        name="Ethan Nguyen",
        email="ethan.nguyen@example.com",
        phone="+1-555-555-0104",
        date_of_birth=date(2020, 6, 15),
        notes="Pediatric patient. Vaccination schedule up to date.",
    ),
    dict(
        id=uuid.UUID("bbbb1111-0000-0000-0000-000000000005"),
        name="Rosa Hernandez",
        email="rosa.hernandez@example.com",
        phone="+1-555-555-0105",
        date_of_birth=date(1990, 1, 30),
        notes="Adult patient. Follow-up for blood pressure monitoring.",
    ),
    dict(
        id=uuid.UUID("bbbb1111-0000-0000-0000-000000000006"),
        name="Liam Thompson",
        email="liam.thompson@example.com",
        date_of_birth=date(2015, 9, 8),
        notes="Pediatric patient. New patient intake completed.",
    ),
]

# Patients who get a login on the patient portal (password: BAPC_SEED_PATIENT_PASSWORD).
SEED_PATIENT_LOGINS = ["maria.garcia@example.com", "james.wilson@example.com"]


def _weekday_at(days_from_today: int, hour: int, minute: int = 0) -> datetime:
    """A clinic-local datetime `days_from_today` out, nudged forward off weekends."""
    day = clinic_now().date() + timedelta(days=days_from_today)
    while day.weekday() not in settings.clinic_open_weekdays:
        day += timedelta(days=1 if days_from_today >= 0 else -1)
    return datetime.combine(day, time(hour, minute), tzinfo=clinic_tz())


def _seed_appointments() -> list[dict]:
    return [
        dict(
            id=uuid.UUID("aaaa1111-0000-0000-0000-000000000001"),
            patient_id=uuid.UUID("bbbb1111-0000-0000-0000-000000000003"),
            appointment_type=AppointmentType.checkup,
            scheduled_at=_weekday_at(-30, 9),
            status=AppointmentStatus.completed,
            reason="Well-child visit",
            notes="Growth on track. Next visit in 6 months.",
        ),
        dict(
            id=uuid.UUID("aaaa1111-0000-0000-0000-000000000002"),
            patient_id=uuid.UUID("bbbb1111-0000-0000-0000-000000000003"),
            appointment_type=AppointmentType.vaccination,
            scheduled_at=_weekday_at(7, 10),
            status=AppointmentStatus.scheduled,
            reason="Flu vaccine",
        ),
        dict(
            id=uuid.UUID("aaaa1111-0000-0000-0000-000000000003"),
            patient_id=uuid.UUID("bbbb1111-0000-0000-0000-000000000005"),
            appointment_type=AppointmentType.follow_up,
            scheduled_at=_weekday_at(10, 14),
            status=AppointmentStatus.scheduled,
            reason="Blood pressure follow-up",
        ),
        dict(
            id=uuid.UUID("aaaa1111-0000-0000-0000-000000000004"),
            patient_id=uuid.UUID("bbbb1111-0000-0000-0000-000000000001"),
            appointment_type=AppointmentType.sick_visit,
            scheduled_at=_weekday_at(-10, 11),
            status=AppointmentStatus.cancelled,
            reason="Cold symptoms",
            notes="Cancelled: patient felt better.",
        ),
    ]


async def _user_exists(db, email: str) -> bool:
    found = await db.execute(select(User.id).where(func.lower(User.email) == email.lower()))
    return found.first() is not None


async def seed() -> None:
    async with async_session() as db:
        added_patients = 0
        for data in SEED_PATIENTS:
            if await db.get(Patient, data["id"]) is None:
                db.add(Patient(**data))
                added_patients += 1
        await db.flush()

        added_appointments = 0
        for data in _seed_appointments():
            if await db.get(Appointment, data["id"]) is None:
                db.add(Appointment(duration_minutes=30, **data))
                added_appointments += 1
        await db.flush()

        added_users = 0
        if not await _user_exists(db, settings.seed_admin_email):
            db.add(
                User(
                    email=settings.seed_admin_email.lower(),
                    password_hash=hash_password(settings.seed_admin_password),
                    full_name="Clinic Admin",
                    role=UserRole.admin,
                )
            )
            added_users += 1
        for email in SEED_PATIENT_LOGINS:
            patient = (
                await db.execute(select(Patient).where(func.lower(Patient.email) == email))
            ).scalar_one_or_none()
            if patient is None or await _user_exists(db, email):
                continue
            already_linked = await db.execute(select(User.id).where(User.patient_id == patient.id))
            if already_linked.first() is not None:
                continue
            db.add(
                User(
                    email=email,
                    password_hash=hash_password(settings.seed_patient_password),
                    full_name=patient.name,
                    role=UserRole.patient,
                    patient_id=patient.id,
                )
            )
            added_users += 1

        await db.commit()
        print(
            f"Seed complete: {added_patients} patients, {added_appointments} appointments, "
            f"{added_users} login accounts added (existing rows left unchanged)."
        )


if __name__ == "__main__":
    asyncio.run(seed())
