"""Tests for patient CRUD routes."""

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_create_patient(client: AsyncClient):
    resp = await client.post(
        "/api/patients",
        json={"name": "Jane Doe", "email": "jane@example.com", "phone": "+1-555-0100"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Jane Doe"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_patients(client: AsyncClient):
    resp = await client.get("/api/patients")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_update_patient(client: AsyncClient):
    created = await client.post(
        "/api/patients",
        json={"name": "Original Name", "email": "original@example.com"},
    )
    patient_id = created.json()["id"]

    resp = await client.patch(
        f"/api/patients/{patient_id}",
        json={"name": "Updated Name", "notes": "Prefers morning appointments"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Updated Name"
    assert data["notes"] == "Prefers morning appointments"


@pytest.mark.asyncio
async def test_delete_patient(client: AsyncClient):
    created = await client.post(
        "/api/patients",
        json={"name": "Delete Me", "email": "delete@example.com"},
    )
    patient_id = created.json()["id"]

    resp = await client.delete(f"/api/patients/{patient_id}")
    assert resp.status_code == 204

    resp = await client.get(f"/api/patients/{patient_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_search_patients_by_query(client: AsyncClient):
    await client.post(
        "/api/patients",
        json={"name": "Alice Smith", "email": "alice@example.com"},
    )
    await client.post(
        "/api/patients",
        json={"name": "Bob Jones", "email": "bob@example.com"},
    )

    resp = await client.get("/api/patients?query=Alice")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Alice Smith"


@pytest.mark.asyncio
async def test_patient_not_found(client: AsyncClient):
    fake_id = uuid.uuid4()
    resp = await client.get(f"/api/patients/{fake_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_patient_normalizes_email_and_rejects_duplicates(client: AsyncClient):
    resp = await client.post(
        "/api/patients", json={"name": "  Ana Ruiz ", "email": " Ana.Ruiz@Example.COM "}
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Ana Ruiz"
    assert resp.json()["email"] == "ana.ruiz@example.com"

    dup = await client.post(
        "/api/patients", json={"name": "Other Ana", "email": "ANA.RUIZ@example.com"}
    )
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_create_patient_validation(client: AsyncClient):
    assert (
        await client.post("/api/patients", json={"name": "", "email": "a@b.co"})
    ).status_code == 422
    assert (
        await client.post("/api/patients", json={"name": "X", "email": "not-an-email"})
    ).status_code == 422
    assert (
        await client.post(
            "/api/patients",
            json={"name": "X", "email": "x@b.co", "date_of_birth": "2999-01-01"},
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_update_patient_cannot_null_required_fields(client: AsyncClient):
    created = (await client.post("/api/patients", json={"name": "N", "email": "n@b.co"})).json()
    resp = await client.patch(f"/api/patients/{created['id']}", json={"email": None})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_patient_cascades_to_appointments_and_login(
    client: AsyncClient, db, patient_user
):
    from app.models import Appointment, User
    from tests.conftest import open_slot

    user_id, patient_id = patient_user.id, patient_user.patient_id
    resp = await client.post(
        "/api/appointments",
        json={
            "patient_id": str(patient_id),
            "appointment_type": "checkup",
            "scheduled_at": open_slot(2).isoformat(),
        },
    )
    assert resp.status_code == 201
    appointment_id = resp.json()["id"]

    assert (await client.delete(f"/api/patients/{patient_id}")).status_code == 204
    db.expire_all()
    assert await db.get(Appointment, uuid.UUID(appointment_id)) is None
    assert await db.get(User, user_id) is None
