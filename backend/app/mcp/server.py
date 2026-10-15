"""
MCP (Model Context Protocol) server for BAPC clinic scheduling.

The server runs locally over stdio with direct database access, so it acts with clinic-staff
(admin) privileges. MCP clients show their own approval prompt before each tool call, so write
tools act immediately, but they go through the same validation as the web app and chat
(clinic hours, future-only, overlap detection, allowed status changes).

Resources:
    - bapc://patients      — all registered patients
    - bapc://appointments  — upcoming active (scheduled/rescheduled) appointments

Tools:
    - search_patients
    - get_patient_details
    - list_appointments
    - get_appointment_details
    - schedule_appointment
    - cancel_appointment
    - reschedule_appointment

Run with: python -m app.mcp.server
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime

from mcp.server import Server
from mcp.server.lowlevel import NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Appointment, AppointmentType, Patient
from app.services import scheduling
from app.services.scheduling import SchedulingError

mcp = Server("bapc")

session_factory = async_session

_TYPE_ENUM = [t.value for t in AppointmentType]
_WHEN_DESCRIPTION = (
    "Clinic local time. Either an ISO 8601 datetime (e.g. 2026-10-20T10:30; an explicit UTC "
    "offset is honored) or a phrase such as 'next Tuesday 10:30 AM'."
)


def _text(data: dict | list) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, default=str))]


def _parse_when(value: str) -> datetime:
    value = (value or "").strip()
    try:
        return scheduling.ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return scheduling.parse_clinic_datetime(value)


async def _get_appointment(db: AsyncSession, appointment_id: str) -> Appointment:
    aid = scheduling.parse_uuid(appointment_id)
    appointment = await db.get(Appointment, aid) if aid else None
    if not appointment:
        raise SchedulingError(f"Appointment {appointment_id!r} not found.")
    return appointment


@mcp.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(
            uri="bapc://patients",
            name="Patients",
            description="List of all registered patients at the clinic",
            mimeType="application/json",
        ),
        Resource(
            uri="bapc://appointments",
            name="Upcoming Appointments",
            description="Upcoming appointments with status 'scheduled' or 'rescheduled', by date",
            mimeType="application/json",
        ),
    ]


@mcp.read_resource()
async def read_resource(uri) -> str:
    uri = str(uri)
    async with session_factory() as db:
        if uri == "bapc://patients":
            patients = await scheduling.search_patients(db)
            return json.dumps([scheduling.patient_to_dict(p) for p in patients], indent=2)
        if uri == "bapc://appointments":
            rows = await scheduling.list_appointments(db, upcoming_only=True)
            return json.dumps([scheduling.appointment_to_dict(a, p) for a, p in rows], indent=2)
    raise ValueError(f"Unknown resource: {uri}")


@mcp.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_patients",
            description="Search patients by name or email. Returns id, name, email, phone.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Name or email fragment"}
                },
                "required": [],
            },
        ),
        Tool(
            name="get_patient_details",
            description="Get one patient's full profile by id, exact name, or email.",
            inputSchema={
                "type": "object",
                "properties": {"patient": {"type": "string", "description": "Id, name, or email"}},
                "required": ["patient"],
            },
        ),
        Tool(
            name="list_appointments",
            description="List appointments, filtered by patient, status, from_date, upcoming_only.",
            inputSchema={
                "type": "object",
                "properties": {
                    "patient": {"type": "string", "description": "Patient id, name, or email"},
                    "status": {
                        "type": "string",
                        "enum": ["scheduled", "rescheduled", "completed", "cancelled"],
                    },
                    "from_date": {"type": "string", "description": "ISO 8601 date or datetime"},
                    "upcoming_only": {"type": "boolean", "default": False},
                },
                "required": [],
            },
        ),
        Tool(
            name="get_appointment_details",
            description="Get full details for one appointment by its UUID.",
            inputSchema={
                "type": "object",
                "properties": {"appointment_id": {"type": "string"}},
                "required": ["appointment_id"],
            },
        ),
        Tool(
            name="schedule_appointment",
            description=(
                "Book an appointment for a patient. Must be in the future, within clinic hours "
                f"({scheduling.clinic_hours_description()}), and not overlap the patient's other "
                "active appointments."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "patient": {"type": "string", "description": "Patient id, name, or email"},
                    "appointment_type": {"type": "string", "enum": _TYPE_ENUM},
                    "scheduled_at": {"type": "string", "description": _WHEN_DESCRIPTION},
                    "duration_minutes": {
                        "type": "integer",
                        "default": 30,
                        "minimum": scheduling.MIN_DURATION,
                        "maximum": scheduling.MAX_DURATION,
                    },
                    "reason": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["patient", "appointment_type", "scheduled_at"],
            },
        ),
        Tool(
            name="cancel_appointment",
            description="Cancel an upcoming scheduled/rescheduled appointment.",
            inputSchema={
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["appointment_id"],
            },
        ),
        Tool(
            name="reschedule_appointment",
            description="Move a scheduled/rescheduled appointment to a new date and time.",
            inputSchema={
                "type": "object",
                "properties": {
                    "appointment_id": {"type": "string"},
                    "new_scheduled_at": {"type": "string", "description": _WHEN_DESCRIPTION},
                    "notes": {"type": "string"},
                },
                "required": ["appointment_id", "new_scheduled_at"],
            },
        ),
    ]


async def _search_patients(db: AsyncSession, args: dict) -> dict:
    patients = await scheduling.search_patients(db, args.get("query"))
    return {"count": len(patients), "patients": [scheduling.patient_to_dict(p) for p in patients]}


async def _get_patient_details(db: AsyncSession, args: dict) -> dict:
    patient = await scheduling.resolve_patient(db, args.get("patient") or args.get("patient_id"))
    return scheduling.patient_to_dict(patient, detailed=True)


async def _list_appointments(db: AsyncSession, args: dict) -> dict:
    patient_id = None
    if ref := args.get("patient") or args.get("patient_id"):
        patient_id = (await scheduling.resolve_patient(db, ref)).id
    from_date = None
    if fd := args.get("from_date"):
        try:
            from_date = datetime.fromisoformat(fd.replace("Z", "+00:00"))
        except ValueError as e:
            raise SchedulingError(f"from_date must be ISO 8601, got {fd!r}.") from e
    rows = await scheduling.list_appointments(
        db,
        patient_id=patient_id,
        status=args.get("status") or None,
        from_date=from_date,
        upcoming_only=bool(args.get("upcoming_only", False)),
    )
    return {
        "count": len(rows),
        "appointments": [scheduling.appointment_to_dict(a, p) for a, p in rows],
    }


async def _get_appointment_details(db: AsyncSession, args: dict) -> dict:
    appointment = await _get_appointment(db, args.get("appointment_id", ""))
    patient = await db.get(Patient, appointment.patient_id)
    return scheduling.appointment_to_dict(appointment, patient)


async def _schedule_appointment(db: AsyncSession, args: dict) -> dict:
    patient = await scheduling.resolve_patient(db, args.get("patient") or args.get("patient_id"))
    try:
        duration = int(args.get("duration_minutes") or 30)
    except (TypeError, ValueError) as e:
        raise SchedulingError("duration_minutes must be an integer.") from e
    appointment = await scheduling.create_appointment(
        db,
        scheduling.ScheduleRequest(
            patient_id=patient.id,
            appointment_type=scheduling.normalize_appointment_type(args.get("appointment_type")),
            scheduled_at=_parse_when(args.get("scheduled_at", "")),
            duration_minutes=duration,
            reason=args.get("reason"),
            notes=args.get("notes"),
        ),
    )
    return scheduling.appointment_to_dict(appointment, patient)


async def _cancel_appointment(db: AsyncSession, args: dict) -> dict:
    appointment = await _get_appointment(db, args.get("appointment_id", ""))
    await scheduling.cancel_appointment(db, appointment, reason=args.get("reason"))
    patient = await db.get(Patient, appointment.patient_id)
    return scheduling.appointment_to_dict(appointment, patient)


async def _reschedule_appointment(db: AsyncSession, args: dict) -> dict:
    appointment = await _get_appointment(db, args.get("appointment_id", ""))
    previous = await scheduling.reschedule_appointment(
        db,
        appointment,
        _parse_when(args.get("new_scheduled_at", "")),
        note=args.get("notes"),
    )
    patient = await db.get(Patient, appointment.patient_id)
    return {
        **scheduling.appointment_to_dict(appointment, patient),
        "previous_scheduled_at": previous.isoformat(),
        "previous_when": scheduling.format_local(previous),
    }


_HANDLERS: dict[str, Callable[[AsyncSession, dict], Awaitable[dict]]] = {
    "search_patients": _search_patients,
    "get_patient_details": _get_patient_details,
    "list_appointments": _list_appointments,
    "get_appointment_details": _get_appointment_details,
    "schedule_appointment": _schedule_appointment,
    "cancel_appointment": _cancel_appointment,
    "reschedule_appointment": _reschedule_appointment,
}


@mcp.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name}")
    async with session_factory() as db:
        try:
            return _text(await handler(db, arguments or {}))
        except SchedulingError as e:
            return _text(e.to_dict())


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await mcp.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="bapc",
                server_version="0.2.0",
                # Advertise tools/resources so clients know to list them.
                capabilities=mcp.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
