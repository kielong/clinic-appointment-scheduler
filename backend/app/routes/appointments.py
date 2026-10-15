import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_db
from app.models import Appointment, AppointmentStatus, Patient
from app.schemas import AppointmentCreate, AppointmentRead, AppointmentUpdate
from app.services import scheduling
from app.services.scheduling import SchedulingError

router = APIRouter(
    prefix="/api/appointments", tags=["appointments"], dependencies=[Depends(require_admin)]
)


def _read(appointment: Appointment, patient: Patient | None) -> AppointmentRead:
    return AppointmentRead.model_validate(appointment).model_copy(
        update={"patient_name": patient.name if patient else None}
    )


def _bad_request(e: SchedulingError) -> HTTPException:
    return HTTPException(status_code=422, detail=e.message)


@router.get("", response_model=list[AppointmentRead])
async def list_appointments(
    patient_id: uuid.UUID | None = Query(None),
    status: AppointmentStatus | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    rows = await scheduling.list_appointments(db, patient_id=patient_id, status=status)
    return [_read(a, p) for a, p in rows]


@router.get("/{appointment_id}", response_model=AppointmentRead)
async def get_appointment(appointment_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    appointment = await db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return _read(appointment, await db.get(Patient, appointment.patient_id))


@router.post("", response_model=AppointmentRead, status_code=201)
async def create_appointment(body: AppointmentCreate, db: AsyncSession = Depends(get_db)):
    patient = await db.get(Patient, body.patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    try:
        appointment = await scheduling.create_appointment(
            db,
            scheduling.ScheduleRequest(
                patient_id=body.patient_id,
                appointment_type=body.appointment_type,
                scheduled_at=body.scheduled_at,
                duration_minutes=body.duration_minutes,
                reason=body.reason,
                notes=body.notes,
            ),
        )
    except SchedulingError as e:
        raise _bad_request(e) from e
    return _read(appointment, patient)


@router.patch("/{appointment_id}", response_model=AppointmentRead)
async def update_appointment(
    appointment_id: uuid.UUID,
    body: AppointmentUpdate,
    db: AsyncSession = Depends(get_db),
):
    appointment = await db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    changes = body.model_dump(exclude_unset=True)
    for field in ("scheduled_at", "status", "duration_minutes", "appointment_type"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be null")
    try:
        appointment = await scheduling.update_appointment(db, appointment, changes)
    except SchedulingError as e:
        raise _bad_request(e) from e
    return _read(appointment, await db.get(Patient, appointment.patient_id))


@router.delete("/{appointment_id}", status_code=204)
async def delete_appointment(appointment_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    appointment = await db.get(Appointment, appointment_id)
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    await db.delete(appointment)
    await db.commit()
