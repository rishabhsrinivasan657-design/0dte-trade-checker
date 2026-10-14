from datetime import date, datetime, time

import pytest

from core.expiry import (ET, first_live_expiry_date, is_market_hours, minutes_to_expiry,
                         years_to_expiry)

WED = date(2026, 10, 14)


def at(h, m, d=WED):
    return datetime.combine(d, time(h, m), tzinfo=ET)


def test_minutes_to_close():
    assert minutes_to_expiry(at(9, 30), WED) == 390
    assert minutes_to_expiry(at(15, 55), WED) == 5


def test_after_close_is_zero_not_negative():
    assert minutes_to_expiry(at(16, 30), WED) == 0


def test_years_conversion():
    assert years_to_expiry(at(9, 30), WED) == pytest.approx(390 / 525_600)


def test_timezone_is_respected():
    # 10:00 in London (BST, UTC+1) is 5:00 AM in New York -> 11 hours to the close
    from zoneinfo import ZoneInfo
    london = datetime(2026, 10, 14, 10, 0, tzinfo=ZoneInfo("Europe/London"))
    assert minutes_to_expiry(london, WED) == 11 * 60


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        minutes_to_expiry(datetime(2026, 10, 14, 10, 0), WED)


def test_first_live_expiry():
    assert first_live_expiry_date(at(12, 0)) == WED
    assert first_live_expiry_date(at(16, 0)) == date(2026, 10, 15)


def test_half_day_close():
    assert minutes_to_expiry(at(12, 0), WED, close=time(13, 0)) == 60


def test_market_hours():
    assert is_market_hours(at(12, 0))
    assert not is_market_hours(at(9, 0))
    assert not is_market_hours(at(12, 0, d=date(2026, 10, 17)))   # Saturday
