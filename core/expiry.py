"""Time-to-expiry convention. Pure functions: pass in 'now', get minutes/years back.

Convention: T = calendar minutes until 4:00 PM ET on the expiry date / 525,600 (ACT/365).
For a 0DTE option during market hours every remaining minute is a trading minute,
so there is no overnight/weekend ambiguity. Because we solve IV ourselves with the
same T, price, probability ITM and theta-per-hour don't depend on the convention;
only the displayed IV number does.

Caveat: half-day sessions (e.g. day after Thanksgiving) close at 1:00 PM ET.
Pass close=time(13, 0) for those; we don't detect them automatically.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
MINUTES_PER_YEAR = 365 * 24 * 60   # 525,600


def to_et(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("'now' must be timezone-aware (e.g. datetime.now(ET))")
    return now.astimezone(ET)


def first_live_expiry_date(now: datetime, close: time = MARKET_CLOSE) -> date:
    """Today's options expire at the close; after that, the earliest live expiry is tomorrow or later."""
    now = to_et(now)
    return now.date() if now.time() < close else now.date() + timedelta(days=1)


def minutes_to_expiry(now: datetime, expiry: date, close: time = MARKET_CLOSE) -> float:
    """Minutes from now until the close on the expiry date (never negative)."""
    now = to_et(now)
    expiry_dt = datetime.combine(expiry, close, tzinfo=ET)
    return max((expiry_dt - now).total_seconds() / 60, 0.0)


def years_to_expiry(now: datetime, expiry: date, close: time = MARKET_CLOSE) -> float:
    """The T that goes into Black-Scholes."""
    return minutes_to_expiry(now, expiry, close) / MINUTES_PER_YEAR


def is_market_hours(now: datetime) -> bool:
    """Regular session on a weekday. Doesn't know about holidays."""
    now = to_et(now)
    return now.weekday() < 5 and MARKET_OPEN <= now.time() < MARKET_CLOSE
