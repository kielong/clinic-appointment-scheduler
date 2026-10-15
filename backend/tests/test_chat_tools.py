"""Agent tools, the server-side confirmation gate, and the /api/chat confirmation path.

These tests call the tools directly (no LLM), so they check exactly what the model is
allowed to do with whatever arguments it produces.
"""

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.agent import pending
from app.agent.tools import Grounding, build_tools, final_reply
from app.models import Appointment, AppointmentStatus, AppointmentType, ChatPendingAction, Patient
from app.services import scheduling
from tests.conftest import closed_day, open_slot


def _tools(user, session_factory, latest: str = "", earlier: list[str] | None = None):
    return {
        t.name: t
        for t in build_tools(user, session_factory, Grounding(latest=latest, earlier=earlier or []))
    }


async def _call(tool, **kwargs) -> dict:
    return json.loads(await tool.coroutine(**kwargs))


async def _other_patient(db, name="Olga Other", email="olga@example.com") -> Patient:
    p = Patient(name=name, email=email, date_of_birth=date(1970, 1, 1))
    db.add(p)
    await db.commit()
    return p


async def _appointments(db, patient_id):
    return (
        (await db.execute(select(Appointment).where(Appointment.patient_id == patient_id)))
        .scalars()
        .all()
    )


def _day_text(dt) -> str:
    return dt.date().isoformat()


# --- Tool sets are scoped by role ---


@pytest.mark.asyncio
async def test_patient_and_admin_get_different_tools(patient_user, admin_user, session_factory):
    patient_tools = set(_tools(patient_user, session_factory))
    admin_tools = set(_tools(admin_user, session_factory))
    assert patient_tools == {
        "my_appointments",
        "schedule_appointment",
        "cancel_appointment",
        "reschedule_appointment",
    }
    assert {"search_patients", "get_patient_details", "list_appointments"} <= admin_tools
    # Patient tools have no way to name another patient.
    for tool in _tools(patient_user, session_factory).values():
        assert "patient" not in tool.args and "patient_id" not in tool.args


# --- Proposals do not write; confirmation does ---


@pytest.mark.asyncio
async def test_patient_schedule_proposes_then_executes(patient_user, session_factory, db):
    slot = open_slot(2, 10)
    tools = _tools(patient_user, session_factory, latest="checkup please at 10am")
    result = await _call(
        tools["schedule_appointment"],
        appointment_type="check-up",
        date=_day_text(slot),
        time="10am",
        reason="annual",
    )
    assert result["status"] == "awaiting_confirmation"
    assert "Pat Patient" in result["summary"]
    assert scheduling.format_local(slot) in result["summary"]
    assert final_reply(json.dumps(result)) == result["summary"]
    assert await _appointments(db, patient_user.patient_id) == []

    action = await pending.get_pending(db, patient_user.id)
    message = await pending.execute(db, patient_user, action)
    assert "booked" in message
    [appt] = await _appointments(db, patient_user.patient_id)
    assert appt.scheduled_at == slot
    assert appt.appointment_type == AppointmentType.checkup
    assert await pending.get_pending(db, patient_user.id) is None


@pytest.mark.asyncio
async def test_patient_tools_never_touch_other_patients(patient_user, session_factory, db):
    other = await _other_patient(db)
    other_appt = await scheduling.create_appointment(
        db,
        scheduling.ScheduleRequest(
            patient_id=other.id, appointment_type=AppointmentType.checkup, scheduled_at=open_slot(2)
        ),
    )
    tools = _tools(patient_user, session_factory)
    listing = await _call(tools["my_appointments"], include_past=True)
    assert listing["count"] == 0

    cancel = await _call(tools["cancel_appointment"], appointment_type="checkup")
    assert "error" in cancel
    await db.refresh(other_appt)
    assert other_appt.status == AppointmentStatus.scheduled

    # Even a tampered pending action for someone else's appointment is refused.
    from app.models import PendingActionKind
    from app.services.scheduling import SchedulingError

    await pending.propose(
        db,
        patient_user.id,
        PendingActionKind.cancel,
        {"appointment_id": str(other_appt.id), "reason": None},
        "x",
    )
    with pytest.raises(SchedulingError):
        await pending.execute(db, patient_user, await pending.get_pending(db, patient_user.id))


@pytest.mark.asyncio
async def test_invalid_requests_return_user_facing_reply(patient_user, session_factory, db):
    tools = _tools(patient_user, session_factory)
    closed = await _call(
        tools["schedule_appointment"],
        appointment_type="sick_visit",
        date=_day_text(closed_day()),
        time="10am",
    )
    assert "closed" in closed["error"]
    assert final_reply(json.dumps(closed)) == closed["reply"]

    bad_type = await _call(
        tools["schedule_appointment"], appointment_type="telepathy", date="tomorrow", time="10am"
    )
    assert "not a visit type" in bad_type["error"]
    assert await db.get(ChatPendingAction, patient_user.id) is None


@pytest.mark.asyncio
async def test_null_like_optional_args_are_ignored(patient_user, session_factory):
    slot = open_slot(2, 11)
    tools = _tools(patient_user, session_factory)
    result = await _call(
        tools["schedule_appointment"],
        appointment_type="physical",
        date=_day_text(slot),
        time="11:00",
        reason="None",
    )
    assert result["status"] == "awaiting_confirmation"
    assert "Reason" not in result["summary"]


# --- Grounding: the user's words win over model slips ---


@pytest.mark.asyncio
async def test_hallucinated_time_is_replaced_with_user_time(patient_user, session_factory):
    slot = open_slot(2, 10)
    tools = _tools(
        patient_user, session_factory, latest=f"book a checkup on {_day_text(slot)} at 10am"
    )
    result = await _call(
        tools["schedule_appointment"],
        appointment_type="checkup",
        date=_day_text(slot),
        time="10:30 AM",
    )
    assert scheduling.format_local(slot) in result["summary"]


@pytest.mark.asyncio
async def test_mismatched_weekday_is_corrected(patient_user, session_factory):
    slot = open_slot(2, 9)  # within the next week, so "<weekday>" resolves to this date
    weekday = slot.strftime("%A")
    wrong_day = slot + timedelta(days=1)
    tools = _tools(patient_user, session_factory, latest=f"physical on {weekday} at 9am")
    result = await _call(
        tools["schedule_appointment"],
        appointment_type="physical",
        date=_day_text(wrong_day),
        time="9am",
    )
    assert scheduling.format_local(slot) in result["summary"]


@pytest.mark.asyncio
async def test_reschedule_intent_redirects_schedule_tool(admin_user, session_factory, db):
    patient = await _other_patient(db, "Ethan Nguyen", "ethan@example.com")
    original = await scheduling.create_appointment(
        db,
        scheduling.ScheduleRequest(
            patient_id=patient.id,
            appointment_type=AppointmentType.vaccination,
            scheduled_at=open_slot(2, 14),
        ),
    )
    target = open_slot(5, 8, 30)
    tools = _tools(
        admin_user, session_factory, latest="Reschedule Ethan's vaccination to that day at 8:30am"
    )
    result = await _call(
        tools["schedule_appointment"],
        patient="Ethan Nguyen",
        appointment_type="vaccination",
        date=_day_text(target),
        time="8:30 AM",
    )
    assert "confirm this change" in result["summary"]
    action = await pending.get_pending(db, admin_user.id)
    await pending.execute(db, admin_user, action)
    appts = await _appointments(db, patient.id)
    assert len(appts) == 1  # moved, not duplicated
    assert appts[0].id == original.id
    assert appts[0].scheduled_at == target
    assert appts[0].status == AppointmentStatus.rescheduled


@pytest.mark.asyncio
async def test_duplicate_type_booking_is_flagged(admin_user, session_factory, db):
    patient = await _other_patient(db, "Rosa Hernandez", "rosa@example.com")
    await scheduling.create_appointment(
        db,
        scheduling.ScheduleRequest(
            patient_id=patient.id,
            appointment_type=AppointmentType.follow_up,
            scheduled_at=open_slot(2),
        ),
    )
    tools = _tools(admin_user, session_factory)
    slot = open_slot(6, 9)
    result = await _call(
        tools["schedule_appointment"],
        patient="Rosa",
        appointment_type="follow up",
        date=_day_text(slot),
        time="9am",
    )
    assert "already has a follow-up" in result["summary"]


# --- Admin lookups ---


@pytest.mark.asyncio
async def test_admin_patient_resolution(admin_user, session_factory, db):
    await _other_patient(db, "Maria Garcia", "maria@example.com")
    await _other_patient(db, "Maria Lopez", "mlopez@example.com")
    tools = _tools(admin_user, session_factory)

    ambiguous = await _call(
        tools["schedule_appointment"],
        patient="Maria",
        appointment_type="checkup",
        date=_day_text(open_slot(3)),
        time="10am",
    )
    assert len(ambiguous["candidates"]) == 2
    assert "Maria Lopez" in ambiguous["reply"]

    missing = await _call(tools["get_patient_details"], patient="Nobody Here")
    assert "No patient matches" in missing["error"]
    assert "reply" not in missing  # read-only errors are left to the model

    found = await _call(tools["search_patients"], query="garcia")
    assert [p["name"] for p in found["patients"]] == ["Maria Garcia"]


@pytest.mark.asyncio
async def test_admin_list_appointments_is_lenient(admin_user, session_factory, db):
    patient = await _other_patient(db, "Sofia Martinez", "sofia@example.com")
    await scheduling.create_appointment(
        db,
        scheduling.ScheduleRequest(
            patient_id=patient.id,
            appointment_type=AppointmentType.vaccination,
            scheduled_at=open_slot(2),
        ),
    )
    # The model dropped the patient and put a non-status in `status`.
    tools = _tools(
        admin_user, session_factory, latest="What upcoming appointments does Sofia Martinez have?"
    )
    result = await _call(tools["list_appointments"], status="upcoming_only")
    assert result["count"] == 1
    assert result["appointments"][0]["patient_name"] == "Sofia Martinez"


@pytest.mark.asyncio
async def test_admin_cancel_and_reschedule_disambiguation(admin_user, session_factory, db):
    patient = await _other_patient(db, "James Wilson", "james@example.com")
    for days in (2, 9):  # always on different days
        await scheduling.create_appointment(
            db,
            scheduling.ScheduleRequest(
                patient_id=patient.id,
                appointment_type=AppointmentType.checkup,
                scheduled_at=open_slot(days),
            ),
        )
    tools = _tools(admin_user, session_factory)
    many = await _call(
        tools["cancel_appointment"], patient="James Wilson", appointment_type="checkup"
    )
    assert len(many["candidates"]) == 2

    second = open_slot(9)
    one = await _call(
        tools["cancel_appointment"], patient="James Wilson", current_date=_day_text(second)
    )
    assert one["status"] == "awaiting_confirmation"
    await pending.execute(db, admin_user, await pending.get_pending(db, admin_user.id))
    statuses = sorted(a.status.value for a in await _appointments(db, patient.id))
    assert statuses == ["cancelled", "scheduled"]


# --- /api/chat confirmation path (no LLM involved) ---


async def _post_chat(client, message: str) -> str:
    text = ""
    async with client.stream("POST", "/api/chat", json={"message": message}) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if data["type"] == "token":
                    text += data["content"]
    return text


@pytest.mark.asyncio
async def test_chat_yes_executes_pending_action(patient_client, patient_user, session_factory, db):
    slot = open_slot(3, 13)
    tools = _tools(patient_user, session_factory)
    await _call(
        tools["schedule_appointment"], appointment_type="checkup", date=_day_text(slot), time="1pm"
    )

    reply = await _post_chat(patient_client, "Yes, please")
    assert "booked" in reply
    assert len(await _appointments(db, patient_user.patient_id)) == 1

    # A second "yes" must not book again.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.routes.chat.build_agent", _FakeAgent.build)
        await _post_chat(patient_client, "yes")
    assert len(await _appointments(db, patient_user.patient_id)) == 1


@pytest.mark.asyncio
async def test_chat_no_discards_pending_action(patient_client, patient_user, session_factory, db):
    tools = _tools(patient_user, session_factory)
    await _call(
        tools["schedule_appointment"],
        appointment_type="checkup",
        date=_day_text(open_slot(3)),
        time="10am",
    )
    reply = await _post_chat(patient_client, "no")
    assert "haven't made any changes" in reply
    assert await db.get(ChatPendingAction, patient_user.id) is None
    assert await _appointments(db, patient_user.patient_id) == []


@pytest.mark.asyncio
async def test_chat_streams_server_confirmation_text(patient_client, patient_user, session_factory):
    """When a tool proposes a change, the route replaces model text with the server summary."""
    summary_holder = {}

    async def fake_events(*_args, **_kwargs):
        tools = _tools(patient_user, session_factory)
        output = await tools["schedule_appointment"].coroutine(
            appointment_type="checkup", date=_day_text(open_slot(3)), time="10am"
        )
        summary_holder["summary"] = json.loads(output)["summary"]
        yield {"event": "on_chat_model_stream", "data": {"chunk": _Chunk("I booked it!")}}
        yield {"event": "on_tool_start", "name": "schedule_appointment", "data": {"input": {}}}
        yield {"event": "on_tool_end", "name": "schedule_appointment", "data": {"output": output}}
        yield {"event": "on_chat_model_stream", "data": {"chunk": _Chunk("All done, booked!")}}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.routes.chat.build_agent", lambda *a, **k: _FakeAgent(fake_events))
        text = await _post_chat_raw(patient_client, "book a checkup")
    assert text == summary_holder["summary"]


class _Chunk:
    def __init__(self, content):
        self.content = content


class _FakeAgent:
    def __init__(self, events=None):
        self._events = events

    @classmethod
    def build(cls, *_a, **_k):
        async def nothing(*_args, **_kwargs):
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": _Chunk("Nothing to confirm.")},
            }

        return cls(nothing)

    def astream_events(self, *args, **kwargs):
        return self._events(*args, **kwargs)


async def _post_chat_raw(client, message: str) -> str:
    """Apply reset events the same way the browser does."""
    text = ""
    async with client.stream("POST", "/api/chat", json={"message": message}) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if data["type"] == "reset":
                    text = ""
                elif data["type"] == "token":
                    text += data["content"]
    return text
