"""What-if map: profit/loss for a grid of SPY prices × clock times until the close.

Value at each cell = Black-Scholes price with the option's CURRENT implied vol
("sticky strike" assumption), minus what you paid. At 4:00 PM it's just intrinsic value.
Caveat: real implied vol shifts when SPY moves, so cells away from today's price are estimates.
"""
from datetime import date, datetime, time, timedelta
from math import sqrt

from core.black_scholes import bs_price
from core.expiry import ET, MARKET_CLOSE, MARKET_OPEN, MINUTES_PER_YEAR

PRICE_STEPS = [0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0]
MAX_ROWS = 17
RANGE_SDS = 2.0          # cover ±2 one-standard-deviation moves to the close


def price_levels(spot: float, one_sd: float) -> list[float]:
    """Round-number SPY prices covering spot ± 2 typical moves, at most 17 rows."""
    span = RANGE_SDS * one_sd
    step = next((s for s in PRICE_STEPS if 2 * span / s <= MAX_ROWS - 1), PRICE_STEPS[-1])
    center = round(spot / step) * step
    n = int(span // step) + 1
    n = min(n, (MAX_ROWS - 1) // 2)
    return [round(center + i * step, 2) for i in range(n, -n - 1, -1)]      # high -> low (top row = highest)


def clock_times(now: datetime, expiry: date) -> list[datetime]:
    """Now, then round half-hours (or hours if far away), ending at the 4 PM close on expiry."""
    now = now.astimezone(ET)
    close = datetime.combine(expiry, MARKET_CLOSE, tzinfo=ET)
    start = now if is_live_session(now, expiry) else datetime.combine(expiry, MARKET_OPEN, tzinfo=ET)
    if start >= close:
        return [close]
    step = timedelta(minutes=30 if (close - start) <= timedelta(hours=3, minutes=30) else 60)
    t = datetime.combine(expiry, time(start.hour, 0), tzinfo=ET)
    times = [start]
    while True:
        t += step
        if t >= close:
            break
        if t - start >= timedelta(minutes=10):
            times.append(t)
    return times + [close]


def scenario_grid(spot: float, strike: float, option_type: str, iv: float, r: float, premium: float,
                  now: datetime, expiry: date) -> dict:
    close = datetime.combine(expiry, MARKET_CLOSE, tzinfo=ET)
    times = clock_times(now, expiry)
    mins = [max((close - t).total_seconds() / 60, 0.0) for t in times]
    one_sd_close = spot * iv * sqrt(max(mins[0], 1) / MINUTES_PER_YEAR)
    prices = price_levels(spot, one_sd_close)
    pnl = [[bs_price(S, strike, m / MINUTES_PER_YEAR, r, iv, option_type) - premium for m in mins] for S in prices]
    return {
        "prices": prices,
        "times": [t.strftime("%-I:%M") for t in times],
        "minutes_left": mins,
        "pnl": pnl,                     # per share; ×100 for one contract
        "spot": spot, "strike": strike, "premium": premium, "iv": iv,
        "first_is_now": is_live_session(now, expiry),   # False -> first column is the 9:30 open on expiry day
    }


def is_live_session(now: datetime, expiry: date) -> bool:
    now = now.astimezone(ET)
    return now.date() == expiry and MARKET_OPEN <= now.time() < MARKET_CLOSE
