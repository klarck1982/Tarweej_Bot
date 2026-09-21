from datetime import timezone

import pytest

from app.services import scheduled as SD


def test_validate_scheduled_package():
    p = SD.validate_package({
        "code": "design30",
        "title": "تصميم يومي 30",
        "description": "30 تصميم + 30 نص",
        "price_usd": "30",
        "total_items": 30,
        "duration_days": 30,
        "send_time": "20:00",
        "include_copy": True,
        "enabled": True,
    })
    assert p["code"] == "design30"
    assert p["price_usd"] == "30.00"
    assert p["total_items"] == 30
    assert p["send_time"] == "20:00"


def test_schedule_times_use_damascus_timezone_and_utc_storage():
    start, times = SD.schedule_times(3, "2026-09-21", "20:00", "Asia/Damascus")
    assert start.tzinfo == timezone.utc
    assert len(times) == 3
    assert times[1] > times[0]
    assert (times[1] - times[0]).total_seconds() == 24 * 3600


def test_validate_rejects_duration_shorter_than_items():
    with pytest.raises(ValueError):
        SD.validate_package({
            "code": "badpkg",
            "title": "باقة",
            "price_usd": 10,
            "total_items": 10,
            "duration_days": 5,
            "send_time": "20:00",
        })
