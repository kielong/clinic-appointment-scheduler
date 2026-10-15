"""Pure parsing/normalization helpers in the scheduling service."""

from datetime import time, timedelta

import pytest

from app.models import AppointmentType
from app.services import scheduling
from app.services.scheduling import SchedulingError


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("checkup", AppointmentType.checkup),
        ("Check-up", AppointmentType.checkup),
        ("well child visit", AppointmentType.checkup),
        ("follow-up", AppointmentType.follow_up),
        ("I have a cough", AppointmentType.sick_visit),
        ("flu shot", AppointmentType.vaccination),
        ("annual exam", AppointmentType.physical),
        ("New Patient", AppointmentType.new_patient),
    ],
)
def test_normalize_appointment_type(raw, expected):
    assert scheduling.normalize_appointment_type(raw) == expected


def test_normalize_appointment_type_rejects_unknown():
    with pytest.raises(SchedulingError):
        scheduling.normalize_appointment_type("telepathy")


@pytest.mark.parametrize(
    "date_text,time_text,expected_time",
    [
        ("tomorrow", "10am", time(10, 0)),
        ("tomorrow", "2:30 PM", time(14, 30)),
        ("tomorrow", "noon", time(12, 0)),
        ("tomorrow", "3", time(15, 0)),
        ("tomorrow", "14:00", time(14, 0)),
        ("tomorrow at 9am", "", time(9, 0)),
    ],
)
def test_parse_clinic_datetime_times(date_text, time_text, expected_time):
    dt = scheduling.parse_clinic_datetime(date_text, time_text)
    assert dt.time() == expected_time
    assert dt.tzinfo is not None
    assert dt.date() == scheduling.clinic_now().date() + timedelta(days=1)


def test_parse_clinic_datetime_ignores_spurious_utc_offset():
    dt = scheduling.parse_clinic_datetime("2030-03-05T10:00:00Z")
    assert dt.hour == 10
    assert dt.utcoffset() == dt.astimezone(scheduling.clinic_tz()).utcoffset()


def test_weekday_names_resolve_to_upcoming_day():
    today = scheduling.clinic_now().date()
    for phrase in ("monday", "this Monday", "next monday", "Monday"):
        day = scheduling.parse_clinic_date(phrase)
        assert day.weekday() == 0
        assert 0 < (day - today).days <= 7


def test_parse_errors_are_user_facing():
    with pytest.raises(SchedulingError, match="time is required"):
        scheduling.parse_clinic_datetime("tomorrow")
    with pytest.raises(SchedulingError, match="couldn't understand the time"):
        scheduling.parse_clinic_datetime("tomorrow", "teatime")


def test_format_local():
    dt = scheduling.parse_clinic_datetime("2030-03-05", "9:05 am")
    assert scheduling.format_local(dt) == "Tuesday, March 5, 2030 at 9:05 AM CST"
