from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session, destroy_session, get_current_user
from app.database import get_db
from app.models import Patient, User, UserRole
from app.schemas import LoginRequest, RegisterRequest, UserRead
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Compared against when the email is unknown, so response time doesn't reveal which emails exist.
_DUMMY_HASH = hash_password("timing-equalizer")


@router.post("/login", response_model=UserRead)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    user = (
        await db.execute(select(User).where(func.lower(User.email) == body.email))
    ).scalar_one_or_none()
    password_ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not password_ok or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    await create_session(db, user, response)
    return user


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    await destroy_session(db, request, response)
    response.status_code = 204
    return response


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(get_current_user)):
    return user


@router.post("/register", response_model=UserRead, status_code=201)
async def register(body: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """Patient self-registration.

    If the clinic already has a patient record with this email (created by staff), the account is
    linked to it only when the date of birth matches; otherwise a new patient record is created.
    """
    existing_user = (
        await db.execute(select(User).where(func.lower(User.email) == body.email))
    ).scalar_one_or_none()
    if existing_user:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    patient = (
        await db.execute(select(Patient).where(func.lower(Patient.email) == body.email))
    ).scalar_one_or_none()
    if patient:
        if patient.date_of_birth != body.date_of_birth:
            raise HTTPException(
                status_code=409,
                detail="This email is already on file with the clinic. Please call the clinic "
                "to set up your account.",
            )
    else:
        patient = Patient(
            name=body.name,
            email=body.email,
            phone=body.phone or None,
            date_of_birth=body.date_of_birth,
        )
        db.add(patient)
        await db.flush()

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=patient.name,
        role=UserRole.patient,
        patient_id=patient.id,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="An account with this email already exists"
        ) from e
    await create_session(db, user, response)
    return user
