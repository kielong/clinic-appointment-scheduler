"""MCP server tools share the scheduling service's validation."""

import json
from datetime import datetime

import pytest

from app.mcp import server
from app.models import Patient
from tests.conftest import closed_day, open_slot


@pytest.fixture(autouse=True)
def _use_test_session(monkeypatch, session_factory):
    monkeypatch.setattr(server, "session_factory", session_factory)


async def _call(name: str, **args) -> dict:
    [content] = await server.call_tool(name, args)
    return json.loads(content.text)


@pytest.fixture
async def patient(db) -> Patient:
    p = Patient(name="Mcp Person", email="mcp@example.com")
    db.add(p)
    await db.commit()
    return p


@pytest.mark.asyncio
async def test_server_advertises_tools_and_resources():
    from mcp.server.lowlevel import NotificationOptions

    caps = server.mcp.get_capabilities(NotificationOptions(), {})
    assert caps.tools is not None
    assert caps.resources is not None
    names = {t.name for t in await server.list_tools()}
    assert names == set(server._HANDLERS)


@pytest.mark.asyncio
async def test_schedule_reschedule_cancel_roundtrip(patient):
    start = open_slot(2, 9)
    booked = await _call(
        "schedule_appointment",
        patient="Mcp Person",
        appointment_type="checkup",
        scheduled_at=start.isoformat(),
    )
    assert booked["status"] == "scheduled", booked
    assert booked["patient_name"] == "Mcp Person"

    moved_to = open_slot(4, 15)
    moved = await _call(
        "reschedule_appointment",
        appointment_id=booked["id"],
        new_scheduled_at=moved_to.isoformat(),
    )
    assert moved["status"] == "rescheduled"
    assert datetime.fromisoformat(moved["previous_scheduled_at"]) == start
    assert datetime.fromisoformat(moved["scheduled_at"]) == moved_to

    cancelled = await _call("cancel_appointment", appointment_id=booked["id"], reason="test")
    assert cancelled["status"] == "cancelled"

    again = await _call("cancel_appointment", appointment_id=booked["id"])
    assert "already cancelled" in again["error"]

    details = await _call("get_appointment_details", appointment_id=booked["id"])
    assert "Cancelled: test" in details["notes"]


@pytest.mark.asyncio
async def test_mcp_enforces_same_rules_as_app(patient):
    weekend = await _call(
        "schedule_appointment",
        patient=str(patient.id),
        appointment_type="checkup",
        scheduled_at=closed_day().isoformat(),
    )
    assert "closed" in weekend["error"]

    bad_type = await _call(
        "schedule_appointment",
        patient=str(patient.id),
        appointment_type="telepathy",
        scheduled_at=open_slot().isoformat(),
    )
    assert "error" in bad_type

    first = await _call(
        "schedule_appointment",
        patient=str(patient.id),
        appointment_type="checkup",
        scheduled_at=open_slot(2, 10).isoformat(),
    )
    overlap = await _call(
        "schedule_appointment",
        patient=str(patient.id),
        appointment_type="physical",
        scheduled_at=open_slot(2, 10, 15).isoformat(),
    )
    assert "overlaps" in overlap["error"]
    assert first["status"] == "scheduled"

    missing = await _call("get_appointment_details", appointment_id="not-a-uuid")
    assert "not found" in missing["error"]


@pytest.mark.asyncio
async def test_mcp_accepts_natural_language_times(patient):
    slot = open_slot(2, 14)
    booked = await _call(
        "schedule_appointment",
        patient="mcp@example.com",
        appointment_type="vaccination",
        scheduled_at=f"{slot.date().isoformat()} 2pm",
    )
    assert datetime.fromisoformat(booked["scheduled_at"]) == slot


@pytest.mark.asyncio
async def test_resources_and_listing(patient):
    await _call(
        "schedule_appointment",
        patient=str(patient.id),
        appointment_type="checkup",
        scheduled_at=open_slot(3).isoformat(),
    )
    upcoming = json.loads(await server.read_resource("bapc://appointments"))
    assert [a["patient_name"] for a in upcoming] == ["Mcp Person"]
    patients = json.loads(await server.read_resource("bapc://patients"))
    assert any(p["email"] == "mcp@example.com" for p in patients)
    listed = await _call("list_appointments", patient="Mcp Person", status="scheduled")
    assert listed["count"] == 1
