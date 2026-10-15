import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, field_validator

from app.models import AppointmentStatus, AppointmentType, UserRole

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _not_future(value: date | None) -> date | None:
    if value is not None and value > date.today():
        raise ValueError("date_of_birth cannot be in the future")
    return value


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=255, pattern=EMAIL_PATTERN),
    AfterValidator(_normalize_email),
]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]
DateOfBirth = Annotated[date, AfterValidator(_not_future)]
Duration = Annotated[int, Field(ge=5, le=240)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=512)]


# --- Patient ---


class PatientBase(BaseModel):
    name: Name
    email: Email
    phone: Phone | None = None
    date_of_birth: DateOfBirth | None = None
    notes: str | None = None


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    name: Name | None = None
    email: Email | None = None
    phone: Phone | None = None
    date_of_birth: DateOfBirth | None = None
    notes: str | None = None


class PatientRead(PatientBase):
    id: uuid.UUID
    created_at: datetime
    has_account: bool = False

    model_config = {"from_attributes": True}


# --- Appointment ---


class AppointmentBase(BaseModel):
    appointment_type: AppointmentType
    scheduled_at: datetime
    duration_minutes: Duration = 30
    reason: Reason | None = None
    notes: str | None = None


class AppointmentCreate(AppointmentBase):
    patient_id: uuid.UUID


class AppointmentUpdate(BaseModel):
    scheduled_at: datetime | None = None
    status: AppointmentStatus | None = None
    duration_minutes: Duration | None = None
    appointment_type: AppointmentType | None = None
    reason: Reason | None = None
    notes: str | None = None


class AppointmentRead(AppointmentBase):
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str | None = None
    status: AppointmentStatus
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Auth ---


class LoginRequest(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    name: Name
    email: Email
    password: str = Field(min_length=8, max_length=256)
    date_of_birth: DateOfBirth
    phone: Phone | None = None


class UserRead(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    patient_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


# --- Chat ---


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=50)
    user_timezone: str | None = None

    @field_validator("message")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message cannot be blank")
        return value
