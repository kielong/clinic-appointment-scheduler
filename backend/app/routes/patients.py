import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.database import get_db
from app.models import Patient, User
from app.schemas import PatientCreate, PatientRead, PatientUpdate
from app.services import scheduling

router = APIRouter(prefix="/api/patients", tags=["patients"], dependencies=[Depends(require_admin)])


async def _with_account_flags(db: AsyncSession, patients: list[Patient]) -> list[PatientRead]:
    ids = [p.id for p in patients]
    linked = set()
    if ids:
        linked = set(
            (await db.execute(select(User.patient_id).where(User.patient_id.in_(ids))))
            .scalars()
            .all()
        )
    return [
        PatientRead.model_validate(p).model_copy(update={"has_account": p.id in linked})
        for p in patients
    ]


async def _commit_or_conflict(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        if "uq_patients_email_lower" in str(e.orig):
            raise HTTPException(
                status_code=409, detail="A patient with this email already exists"
            ) from e
        raise HTTPException(status_code=409, detail="Patient data violates a constraint") from e


@router.get("", response_model=list[PatientRead])
async def list_patients(
    query: str | None = Query(None, description="Search by name or email"),
    db: AsyncSession = Depends(get_db),
):
    return await _with_account_flags(db, await scheduling.search_patients(db, query))


@router.get("/count")
async def count_patients(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(func.count(Patient.id)))
    return {"count": result.scalar()}


@router.get("/{patient_id}", response_model=PatientRead)
async def get_patient(patient_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    patient = await db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return (await _with_account_flags(db, [patient]))[0]


@router.post("", response_model=PatientRead, status_code=201)
async def create_patient(body: PatientCreate, db: AsyncSession = Depends(get_db)):
    patient = Patient(**body.model_dump())
    db.add(patient)
    await _commit_or_conflict(db)
    await db.refresh(patient)
    return patient


@router.patch("/{patient_id}", response_model=PatientRead)
async def update_patient(
    patient_id: uuid.UUID,
    body: PatientUpdate,
    db: AsyncSession = Depends(get_db),
):
    patient = await db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    changes = body.model_dump(exclude_unset=True)
    for field in ("name", "email"):
        if field in changes and changes[field] is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be empty")
    for field, value in changes.items():
        setattr(patient, field, value)
    # Keep a linked login's display name in sync with the patient record.
    account = (
        await db.execute(select(User).where(User.patient_id == patient.id))
    ).scalar_one_or_none()
    if account and "name" in changes:
        account.full_name = changes["name"]

    await _commit_or_conflict(db)
    await db.refresh(patient)
    return (await _with_account_flags(db, [patient]))[0]


@router.delete("/{patient_id}", status_code=204)
async def delete_patient(patient_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    patient = await db.get(Patient, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    # Appointments, the linked login, and its sessions are removed by ON DELETE CASCADE.
    await db.delete(patient)
    await db.commit()
