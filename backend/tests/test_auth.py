"""Login, registration, and role-based access control."""

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models import Patient, User, UserRole
from tests.conftest import ADMIN_PASSWORD, PATIENT_PASSWORD


@pytest.mark.asyncio
async def test_login_sets_cookie_and_me_returns_user(anon_client: AsyncClient, admin_user):
    resp = await anon_client.post(
        "/api/auth/login", json={"email": "ADMIN@clinic.test", "password": ADMIN_PASSWORD}
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"
    assert "bapc_session" in resp.cookies
    assert "httponly" in resp.headers["set-cookie"].lower()

    me = await anon_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "admin@clinic.test"


@pytest.mark.asyncio
async def test_login_rejects_bad_password(anon_client: AsyncClient, admin_user):
    resp = await anon_client.post(
        "/api/auth/login", json={"email": "admin@clinic.test", "password": "wrong"}
    )
    assert resp.status_code == 401
    resp = await anon_client.post(
        "/api/auth/login", json={"email": "nobody@clinic.test", "password": "wrong"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_invalidates_session(client: AsyncClient):
    assert (await client.get("/api/auth/me")).status_code == 200
    assert (await client.post("/api/auth/logout")).status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_forged_cookie_is_rejected(anon_client: AsyncClient):
    anon_client.cookies.set("bapc_session", "forged-token")
    assert (await anon_client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/patients", "/api/appointments", "/api/patients/count"])
async def test_anonymous_cannot_read_clinic_data(anon_client: AsyncClient, path: str):
    assert (await anon_client.get(path)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/patients", "/api/appointments", "/api/patients/count"])
async def test_patient_cannot_read_clinic_data(patient_client: AsyncClient, path: str):
    assert (await patient_client.get(path)).status_code == 403


@pytest.mark.asyncio
async def test_patient_cannot_write_clinic_data(patient_client: AsyncClient, patient_user):
    resp = await patient_client.post("/api/patients", json={"name": "X", "email": "x@y.co"})
    assert resp.status_code == 403
    resp = await patient_client.delete(f"/api/patients/{patient_user.patient_id}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_chat_requires_login(anon_client: AsyncClient):
    resp = await anon_client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_register_creates_patient_and_account(anon_client: AsyncClient, db):
    resp = await anon_client.post(
        "/api/auth/register",
        json={
            "name": "New Person",
            "email": "New.Person@Example.com",
            "password": "long-enough-pw",
            "date_of_birth": "1999-02-03",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "patient"
    assert body["email"] == "new.person@example.com"
    patient = await db.get(Patient, body["patient_id"])
    assert patient.name == "New Person"
    # Registration also signs the user in.
    assert (await anon_client.get("/api/auth/me")).json()["role"] == "patient"


@pytest.mark.asyncio
async def test_register_links_existing_patient_only_with_matching_dob(anon_client: AsyncClient, db):
    existing = Patient(name="On File", email="onfile@example.com", date_of_birth=date(1980, 1, 1))
    db.add(existing)
    await db.commit()

    payload = {
        "name": "On File",
        "email": "onfile@example.com",
        "password": "long-enough-pw",
        "date_of_birth": "1981-01-01",
    }
    assert (await anon_client.post("/api/auth/register", json=payload)).status_code == 409

    payload["date_of_birth"] = "1980-01-01"
    resp = await anon_client.post("/api/auth/register", json=payload)
    assert resp.status_code == 201
    assert resp.json()["patient_id"] == str(existing.id)
    patients = (
        (await db.execute(select(Patient).where(Patient.email == "onfile@example.com")))
        .scalars()
        .all()
    )
    assert len(patients) == 1


@pytest.mark.asyncio
async def test_register_rejects_existing_account_and_weak_password(
    anon_client: AsyncClient, patient_user
):
    resp = await anon_client.post(
        "/api/auth/register",
        json={
            "name": "Pat",
            "email": "pat@example.com",
            "password": PATIENT_PASSWORD,
            "date_of_birth": "1990-05-01",
        },
    )
    assert resp.status_code == 409
    resp = await anon_client.post(
        "/api/auth/register",
        json={
            "name": "Z",
            "email": "z@example.com",
            "password": "short",
            "date_of_birth": "1990-05-01",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_user_role_patient_link_is_enforced_by_database(db):
    from sqlalchemy.exc import IntegrityError

    db.add(User(email="bad@x.co", password_hash="x", full_name="Bad", role=UserRole.patient))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()
