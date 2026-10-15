"""Appointment HTTP routes, scheduling rules, and database-level integrity."""

import uuid
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError

from app.models import Appointment, AppointmentStatus, AppointmentType
from tests.conftest import closed_day, open_slot


async def _make_patient(
    client: AsyncClient, *, name: str = "Jane Doe", email: str = "jane@example.com"
):
    resp = await client.post(
        "/api/patients",
        json={"name": name, "email": email, "phone": "+1-555-0100"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _make_appointment(client: AsyncClient, patient_id: str, **overrides):
    body = {
        "patient_id": patient_id,
        "appointment_type": "checkup",
        "scheduled_at": open_slot(2).isoformat(),
        "duration_minutes": 30,
    }
    body.update(overrides)
    r = await client.post("/api/appointments", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# --- Route tests ---


@pytest.mark.asyncio
async def test_list_appointments_returns_list_with_patient_names(client: AsyncClient):
    patient_id = await _make_patient(client)
    await _make_appointment(client, patient_id)
    resp = await client.get("/api/appointments")
    assert resp.status_code == 200
    assert resp.json()[0]["patient_name"] == "Jane Doe"


@pytest.mark.asyncio
async def test_list_appointments_filter_by_patient_id(client: AsyncClient):
    patient_a = await _make_patient(client)
    patient_b = await _make_patient(client, name="Bob Roe", email="bob@example.com")

    await _make_appointment(client, patient_a)
    await _make_appointment(client, patient_b)

    data = (await client.get(f"/api/appointments?patient_id={patient_a}")).json()
    assert len(data) == 1
    assert data[0]["patient_id"] == patient_a


@pytest.mark.asyncio
async def test_list_appointments_filter_by_status(client: AsyncClient):
    patient_id = await _make_patient(client)
    created = await _make_appointment(client, patient_id, scheduled_at=open_slot(2).isoformat())
    await client.patch(f"/api/appointments/{created['id']}", json={"status": "cancelled"})
    await _make_appointment(client, patient_id, scheduled_at=open_slot(3, 11).isoformat())

    scheduled = (
        await client.get(f"/api/appointments?patient_id={patient_id}&status=scheduled")
    ).json()
    cancelled = (
        await client.get(f"/api/appointments?patient_id={patient_id}&status=cancelled")
    ).json()
    assert len(scheduled) == 1
    assert len(cancelled) == 1
    assert (await client.get("/api/appointments?status=bogus")).status_code == 422


@pytest.mark.asyncio
async def test_create_appointment_success(client: AsyncClient):
    patient_id = await _make_patient(client)

    resp = await client.post(
        "/api/appointments",
        json={
            "patient_id": patient_id,
            "appointment_type": "sick_visit",
            "scheduled_at": open_slot(1, 10).isoformat(),
        },
    )

    assert resp.status_code == 201
    data = resp.json()
    assert data["patient_id"] == patient_id
    assert data["status"] == "scheduled"
    assert data["duration_minutes"] == 30


@pytest.mark.asyncio
async def test_create_appointment_patient_not_found(client: AsyncClient):
    resp = await client.post(
        "/api/appointments",
        json={
            "patient_id": str(uuid.uuid4()),
            "appointment_type": "checkup",
            "scheduled_at": open_slot().isoformat(),
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"appointment_type": "telepathy"},
        {"duration_minutes": 0},
        {"duration_minutes": 600},
    ],
)
async def test_create_appointment_rejects_invalid_fields(client: AsyncClient, overrides):
    patient_id = await _make_patient(client)
    body = {
        "patient_id": patient_id,
        "appointment_type": "checkup",
        "scheduled_at": open_slot().isoformat(),
        **overrides,
    }
    assert (await client.post("/api/appointments", json=body)).status_code == 422


@pytest.mark.asyncio
async def test_create_appointment_enforces_calendar_rules(client: AsyncClient):
    patient_id = await _make_patient(client)

    def body(when):
        return {"patient_id": patient_id, "appointment_type": "checkup", "scheduled_at": when}

    past = await client.post("/api/appointments", json=body("2020-01-06T10:00:00-06:00"))
    assert past.status_code == 422
    assert "past" in past.json()["detail"]

    weekend = await client.post("/api/appointments", json=body(closed_day().isoformat()))
    assert weekend.status_code == 422
    assert "closed" in weekend.json()["detail"]

    early = await client.post("/api/appointments", json=body(open_slot(1, 6).isoformat()))
    assert early.status_code == 422
    assert "outside clinic hours" in early.json()["detail"]

    # 4:45 PM + 30 minutes runs past the 5 PM close.
    late = await client.post("/api/appointments", json=body(open_slot(1, 16, 45).isoformat()))
    assert late.status_code == 422


@pytest.mark.asyncio
async def test_create_appointment_rejects_overlap_for_same_patient(client: AsyncClient):
    patient_id = await _make_patient(client)
    other_id = await _make_patient(client, name="Bob Roe", email="bob@example.com")
    start = open_slot(2, 10)
    await _make_appointment(client, patient_id, scheduled_at=start.isoformat())

    overlap = await client.post(
        "/api/appointments",
        json={
            "patient_id": patient_id,
            "appointment_type": "physical",
            "scheduled_at": (start + timedelta(minutes=15)).isoformat(),
        },
    )
    assert overlap.status_code == 422
    assert "overlaps" in overlap.json()["detail"]

    # Back-to-back is fine, and other patients can book the same time.
    await _make_appointment(
        client, patient_id, scheduled_at=(start + timedelta(minutes=30)).isoformat()
    )
    await _make_appointment(client, other_id, scheduled_at=start.isoformat())


@pytest.mark.asyncio
async def test_cancelled_appointment_frees_the_slot(client: AsyncClient):
    patient_id = await _make_patient(client)
    first = await _make_appointment(client, patient_id)
    await client.patch(f"/api/appointments/{first['id']}", json={"status": "cancelled"})
    await _make_appointment(client, patient_id)  # same slot now succeeds


@pytest.mark.asyncio
async def test_reschedule_via_patch_updates_time_and_status(client: AsyncClient):
    patient_id = await _make_patient(client)
    created = await _make_appointment(client, patient_id, scheduled_at=open_slot(2).isoformat())
    new_time = open_slot(5, 9)

    resp = await client.patch(
        f"/api/appointments/{created['id']}",
        json={"scheduled_at": new_time.isoformat()},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == created["id"]
    assert data["status"] == "rescheduled"
    assert datetime.fromisoformat(data["scheduled_at"]) == new_time


@pytest.mark.asyncio
async def test_status_transitions_are_enforced(client: AsyncClient):
    patient_id = await _make_patient(client)
    appt = await _make_appointment(client, patient_id)
    url = f"/api/appointments/{appt['id']}"

    assert (await client.patch(url, json={"status": "completed"})).status_code == 200
    # Completed visits are final.
    assert (await client.patch(url, json={"status": "scheduled"})).status_code == 422
    assert (
        await client.patch(url, json={"scheduled_at": open_slot(8, 11).isoformat()})
    ).status_code == 422
    # Notes can still be added.
    assert (await client.patch(url, json={"notes": "Seen by Dr. A"})).status_code == 200
    assert (await client.patch(url, json={"status": None})).status_code == 422


@pytest.mark.asyncio
async def test_reinstating_cancelled_appointment_rechecks_conflicts(client: AsyncClient):
    patient_id = await _make_patient(client)
    first = await _make_appointment(client, patient_id)
    await client.patch(f"/api/appointments/{first['id']}", json={"status": "cancelled"})
    await _make_appointment(client, patient_id)  # takes the slot
    resp = await client.patch(f"/api/appointments/{first['id']}", json={"status": "scheduled"})
    assert resp.status_code == 422


# --- Database-level integrity (backstops for the application checks) ---


async def _db_patient(db):
    from app.models import Patient

    p = Patient(name="Db Person", email=f"db-{uuid.uuid4().hex[:8]}@example.com")
    db.add(p)
    await db.flush()
    return p


@pytest.mark.asyncio
async def test_database_rejects_overlapping_active_appointments(db):
    patient = await _db_patient(db)
    start = open_slot(3)
    db.add(
        Appointment(
            patient_id=patient.id, appointment_type=AppointmentType.checkup, scheduled_at=start
        )
    )
    await db.flush()
    db.add(
        Appointment(
            patient_id=patient.id,
            appointment_type=AppointmentType.physical,
            scheduled_at=start + timedelta(minutes=10),
        )
    )
    with pytest.raises(IntegrityError, match="ex_appointments_patient_no_overlap"):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_database_allows_overlap_with_cancelled(db):
    patient = await _db_patient(db)
    start = open_slot(3)
    db.add(
        Appointment(
            patient_id=patient.id,
            appointment_type=AppointmentType.checkup,
            scheduled_at=start,
            status=AppointmentStatus.cancelled,
        )
    )
    db.add(
        Appointment(
            patient_id=patient.id, appointment_type=AppointmentType.checkup, scheduled_at=start
        )
    )
    await db.flush()


@pytest.mark.asyncio
async def test_database_rejects_invalid_status_and_duration(db):
    from sqlalchemy import text

    patient = await _db_patient(db)
    for column, value in (("status", "'bogus'"), ("duration_minutes", "0")):
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(
                    text(
                        "INSERT INTO appointments "
                        f"(id, patient_id, appointment_type, scheduled_at, {column}) "
                        f"VALUES (gen_random_uuid(), :pid, 'checkup', now(), {value})"
                    ),
                    {"pid": patient.id},
                )


@pytest.mark.asyncio
async def test_database_rejects_case_insensitive_duplicate_email(db):
    from app.models import Patient

    db.add(Patient(name="A", email="same@example.com"))
    await db.flush()
    db.add(Patient(name="B", email="SAME@example.com"))
    with pytest.raises(IntegrityError, match="uq_patients_email_lower"):
        await db.flush()
    await db.rollback()
