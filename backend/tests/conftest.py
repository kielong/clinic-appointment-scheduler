"""
Pytest fixtures for the backend test suite.

Uses a dedicated PostgreSQL database (`<dev db name>_test`, created automatically on the
same server as BAPC_DATABASE_URL) with per-test transaction rollback, so tests never touch
development data.
"""

from collections.abc import AsyncGenerator
from datetime import date, datetime, time, timedelta

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app import models  # noqa: F401 — register models
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import Patient, User, UserRole
from app.security import hash_password
from app.services.scheduling import clinic_now, clinic_tz

_dev_url = make_url(settings.database_url)
TEST_DATABASE_URL = _dev_url.set(database=f"{_dev_url.database}_test")

engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)

ADMIN_PASSWORD = "admin-test-password"
PATIENT_PASSWORD = "patient-test-password"


async def _ensure_test_database() -> None:
    conn = await asyncpg.connect(
        user=_dev_url.username,
        password=_dev_url.password,
        host=_dev_url.host,
        port=_dev_url.port or 5432,
        database=_dev_url.database,
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DATABASE_URL.database
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{TEST_DATABASE_URL.database}"')
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def setup_db():
    """Create a fresh schema once for the test session."""
    await _ensure_test_database()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _reset_sse_exit_event():
    """sse_starlette caches an anyio.Event bound to the first event loop; each test has its own."""
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None
    yield
    AppStatus.should_exit_event = None


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session isolated from other tests."""
    async with engine.connect() as conn:
        txn = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if txn.is_active:
                await txn.rollback()


class SessionCtx:
    """Stands in for `async_session` so tools/MCP use the per-test session."""

    def __init__(self, db: AsyncSession):
        self._db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def session_factory(db) -> SessionCtx:
    return SessionCtx(db)


async def _client(db: AsyncSession) -> AsyncClient:
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def anon_client(db: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async with await _client(db) as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_user(db: AsyncSession) -> User:
    user = User(
        email="admin@clinic.test",
        password_hash=hash_password(ADMIN_PASSWORD),
        full_name="Test Admin",
        role=UserRole.admin,
    )
    db.add(user)
    await db.commit()
    return user


@pytest_asyncio.fixture
async def client(db: AsyncSession, admin_user: User) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client signed in as a clinic admin."""
    async with await _client(db) as c:
        resp = await c.post(
            "/api/auth/login", json={"email": admin_user.email, "password": ADMIN_PASSWORD}
        )
        assert resp.status_code == 200, resp.text
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def patient_user(db: AsyncSession) -> User:
    patient = Patient(
        name="Pat Patient",
        email="pat@example.com",
        date_of_birth=date(1990, 5, 1),
    )
    db.add(patient)
    await db.flush()
    user = User(
        email="pat@example.com",
        password_hash=hash_password(PATIENT_PASSWORD),
        full_name=patient.name,
        role=UserRole.patient,
        patient_id=patient.id,
    )
    db.add(user)
    await db.commit()
    return user


@pytest_asyncio.fixture
async def patient_client(db: AsyncSession, patient_user: User) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client signed in as a patient."""
    async with await _client(db) as c:
        resp = await c.post(
            "/api/auth/login", json={"email": patient_user.email, "password": PATIENT_PASSWORD}
        )
        assert resp.status_code == 200, resp.text
        yield c
    app.dependency_overrides.clear()


def open_slot(days_ahead: int = 1, hour: int = 10, minute: int = 0) -> datetime:
    """A clinic-local datetime on an open weekday at least `days_ahead` days from today."""
    day = clinic_now().date() + timedelta(days=days_ahead)
    while day.weekday() not in settings.clinic_open_weekdays:
        day += timedelta(days=1)
    return datetime.combine(day, time(hour, minute), tzinfo=clinic_tz())


def closed_day(days_ahead: int = 1) -> datetime:
    day = clinic_now().date() + timedelta(days=days_ahead)
    while day.weekday() in settings.clinic_open_weekdays:
        day += timedelta(days=1)
    return datetime.combine(day, time(10, 0), tzinfo=clinic_tz())
