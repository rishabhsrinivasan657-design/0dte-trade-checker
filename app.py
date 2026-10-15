"""Web server: a small JSON API over checker/ + the static frontend in web/.

Run locally:  .venv/bin/uvicorn app:app --reload
Endpoints:
  GET /api/market                          spot, expiry, expected move, strikes with quotes
  GET /api/check?type=call&strike=772      the full trade check (+ optional &premium=0.31)
"""
import threading
import time as clock
from dataclasses import asdict
from datetime import date, datetime
from math import isfinite
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from checker.history_check import SESSION_MINUTES, history_check
from checker.trade_check import HOURS_PER_YEAR, check_trade
from core.expiry import MINUTES_PER_YEAR, is_market_hours, minutes_to_expiry
from core.greeks import gamma, theta
from data.chain import MarketSnapshot, expected_move, load_market
from data.history import daily_bars, intraday_bars

load_dotenv()
WEB = Path(__file__).parent / "web"
MARKET_TTL_SECONDS = 15
DISCLAIMER = "Educational tool, not financial advice. Quotes may be delayed or indicative."

app = FastAPI(title="0DTE Trade Checker")

_cache: dict = {"at": 0.0, "market": None}
_lock = threading.Lock()


def market() -> MarketSnapshot:
    """One shared snapshot for 15s, so /market and /check see the same quotes."""
    with _lock:
        if _cache["market"] is None or clock.monotonic() - _cache["at"] > MARKET_TTL_SECONDS:
            try:
                _cache["market"] = load_market()
            except Exception as e:
                raise HTTPException(503, f"Market data unavailable: {e}") from e
            _cache["at"] = clock.monotonic()
        return _cache["market"]


def jsonable(x):
    """dataclasses / numpy / dates / NaN -> plain JSON values."""
    if hasattr(x, "__dataclass_fields__"):
        return jsonable(asdict(x))
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return float(x) if isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (datetime, date, pd.Timestamp)):
        return x.isoformat()
    return x


def greeks_curve(m: MarketSnapshot, strike: float, iv: float, option_type: str,
                 atm_strike: float, atm_iv: float) -> dict:
    """Gamma and theta ($/hour) from 6.5 hours to 5 minutes before the close, SPY held at today's price."""
    minutes = list(range(SESSION_MINUTES, 30, -10)) + list(range(30, 4, -1))
    out = {"minutes": minutes, "series": []}
    for label, k, sigma, cp in [("ATM " + f"{atm_strike:g}", atm_strike, atm_iv, option_type),
                                (f"Your {strike:g} {option_type}", strike, iv, option_type)]:
        Ts = [mn / MINUTES_PER_YEAR for mn in minutes]
        out["series"].append({
            "label": label,
            "gamma": [gamma(m.spot, k, T, m.r, sigma) for T in Ts],
            "theta_per_hour": [theta(m.spot, k, T, m.r, sigma, cp) / HOURS_PER_YEAR for T in Ts],
        })
    return out


@app.get("/api/market")
def api_market():
    m = market()
    em = expected_move(m.chain, m.spot, m.T, m.r)
    quotes = {cp: m.chain.loc[m.chain["type"] == cp, ["strike", "bid", "ask", "mid"]].to_dict("records")
              for cp in ("call", "put")}
    return jsonable({
        "spot": m.spot, "quoted_spot": m.quoted_spot, "expiry": m.expiry, "source": m.source,
        "as_of": m.as_of, "minutes_left": minutes_to_expiry(m.as_of, m.expiry),
        "market_open": is_market_hours(m.as_of), "r": m.r,
        "expected": em, "quotes": quotes, "notes": m.notes, "disclaimer": DISCLAIMER,
    })


@app.get("/api/check")
def api_check(option_type: str = Query(..., alias="type", pattern="^(call|put)$"),
              strike: float = Query(..., gt=0),
              premium: float | None = Query(None, gt=0)):
    m = market()
    try:
        c = check_trade(m, option_type, strike, premium)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    notes = list(m.notes) + list(c.notes)
    today = m.as_of.date()
    try:
        h = history_check(c, daily_bars(today), intraday_bars(today))
        notes += h.notes
    except Exception as e:   # history is a bonus: never block the live numbers on it
        h = None
        notes.append(f"History check unavailable ({type(e).__name__}).")

    trade = asdict(c)
    trade["loss_if_flat"] = c.loss_if_flat
    return jsonable({
        "market": {"spot": m.spot, "expiry": m.expiry, "source": m.source, "as_of": m.as_of,
                   "market_open": is_market_hours(m.as_of)},
        "trade": trade,
        "history": h,
        "greeks_curve": greeks_curve(m, strike, c.iv, option_type, c.expected["atm_strike"], c.expected["atm_iv"]),
        "notes": notes,
        "disclaimer": DISCLAIMER,
    })


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
