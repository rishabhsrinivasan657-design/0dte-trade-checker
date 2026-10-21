"""Web server: a small JSON API over checker/ + the static frontend in web/.

Run locally:  .venv/bin/uvicorn app:app --reload
Endpoints:
  GET /api/market                          spot, expiry, expected move, strikes with quotes
  GET /api/check?type=call&strike=772      the full trade check (+ optional &premium=0.31)
  GET /api/scan?type=call                  quick rating of every strike near SPY (compare strikes)
  GET /api/spy                             SPY 5-minute bars for the current/last session (the price chart)
  GET /api/strategies                      Strategy Lab: today's live setups + backtest results
  GET /research.json                       backtest + model results (made by scripts/run_research.py)
"""
import json
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

from checker.history_check import SESSION_MINUTES, history_check, verdict
from checker import decision as decision_engine
from checker import strategy_signals
from checker.scenarios import scenario_grid
from checker.trade_check import HOURS_PER_YEAR, check_trade
from core.expiry import ET, MINUTES_PER_YEAR, is_market_hours, minutes_to_expiry
from core.black_scholes import prob_itm
from core.greeks import gamma, theta
from data.chain import MarketSnapshot, expected_move, load_market
from data.history import daily_bars, intraday_bars
from research import exits as research_exits
from research import insights as research_insights
from research import live_forecast

load_dotenv()
WEB = Path(__file__).parent / "web"
MARKET_TTL_SECONDS = 15
DISCLAIMER = "Educational tool, not financial advice. Quotes may be delayed or indicative."

app = FastAPI(title="0DTE Trade Checker")


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Browsers must re-check page files on each load (304 if unchanged), so updates show up immediately."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

_cache: dict = {"at": 0.0, "market": None}
_lock = threading.Lock()
MODEL_INPUTS_TTL_SECONDS = 60
_model = {"bundle": live_forecast.load_bundle(), "inputs": None, "at": 0.0}
INSIGHTS_FILE = Path(__file__).parent / "models" / "insights.json"
_insights = json.loads(INSIGHTS_FILE.read_text()) if INSIGHTS_FILE.exists() else None
_model_lock = threading.Lock()
_trade_bundle = decision_engine.load_bundle()
SCAN_WINDOW = 8.0      # must match the strike dropdown in web/app.js


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


def live_inputs():
    """Recent SPY 5-minute bars (incl. today) + VIX, shared by the ML model and the price chart. Refreshed every 60s."""
    with _model_lock:
        if _model["inputs"] is None or clock.monotonic() - _model["at"] > MODEL_INPUTS_TTL_SECONDS:
            _model["inputs"] = live_forecast.fetch_live_inputs()
            _model["at"] = clock.monotonic()
        return _model["inputs"]


def model_check(m: MarketSnapshot, trade) -> dict | None:
    """The ML model's view of this trade: its expected move and chance ITM vs the market's.
    None when there's no trained model or it's outside today's session."""
    bundle = _model["bundle"]
    if bundle is None or not is_market_hours(m.as_of) or trade.minutes_left > SESSION_MINUTES:
        return None
    bars, vix_prev, vix1d_prev = live_inputs()
    feats = live_forecast.live_features(bars, vix_prev, m.as_of, vix1d_prev)
    if feats is None:
        return None
    f = live_forecast.forecast(bundle, feats, m.spot)
    # same units the models were trained on: VIX as a fraction (0.17), move as |log return|
    f["vix_prev"], f["move_so_far"] = feats["vix_prev"], feats["move_so_far"]
    T = trade.minutes_left / MINUTES_PER_YEAR
    p_itm = prob_itm(m.spot, trade.strike, T, m.r, f["vol"], trade.option_type)
    p_profit = prob_itm(m.spot, trade.breakeven, T, m.r, f["vol"], trade.option_type)
    return {**f, "prob_itm": p_itm, "prob_profit": p_profit,
            "market_vol": trade.expected["atm_iv"], "market_move": trade.expected["move"],
            "verdict": verdict(trade.prob_itm, p_itm)}


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


def analyze(m: MarketSnapshot, option_type: str, strike: float, premium: float | None, full: bool = True) -> dict:
    """Everything the checker knows about one option. full=False skips the heavy display-only parts."""
    c = check_trade(m, option_type, strike, premium)
    notes = list(m.notes) + list(c.notes)
    today = m.as_of.date()
    try:
        h = history_check(c, daily_bars(today), intraday_bars(today))
        notes += h.notes
    except Exception as e:   # history is a bonus: never block the live numbers on it
        h = None
        notes.append(f"History check unavailable ({type(e).__name__}).")
    try:
        model = model_check(m, c)
    except Exception as e:   # the model is a bonus too
        model = None
        notes.append(f"Model forecast unavailable ({type(e).__name__}).")

    evidence = None
    if _insights:
        moneyness = (c.strike - c.spot) if option_type == "call" else (c.spot - c.strike)
        evidence = research_insights.evidence(_insights, option_type, moneyness, c.spot, c.expected["atm_iv"],
                                              c.minutes_left, c.prob_itm, c.prob_profit)
        if full and "hold_vs_sell" in _insights:
            evidence["hold_vs_sell"] = research_exits.lookup_hold(_insights["hold_vs_sell"], option_type,
                                                                  evidence["z"], c.minutes_left)
            evidence["exit_rules"] = _insights["exit_rules"]
            evidence["exit_rules_by_group"] = _insights["exit_rules_by_group"]

    decision = decision_engine.decide(
        _trade_bundle, c,
        hist_intraday=h.intraday.itm if h is not None and h.intraday is not None else None,
        ml_ratio=model["vol"] / model["market_vol"] if model else None,
        vix_prev=model["vix_prev"] if model else None,
        move_so_far=model["move_so_far"] if model else None,
        similar=evidence["similar"] if evidence else None)

    out = {"trade": c, "history": h, "model": model, "evidence": evidence, "decision": decision, "notes": notes}
    if full:
        out["scenarios"] = scenario_grid(c.spot, strike, option_type, c.iv, m.r, c.premium, m.as_of, m.expiry)
        out["greeks_curve"] = greeks_curve(m, strike, c.iv, option_type, c.expected["atm_strike"], c.expected["atm_iv"])
    return out


@app.get("/api/check")
def api_check(option_type: str = Query(..., alias="type", pattern="^(call|put)$"),
              strike: float = Query(..., gt=0),
              premium: float | None = Query(None, gt=0)):
    m = market()
    try:
        a = analyze(m, option_type, strike, premium)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    trade = asdict(a["trade"])
    trade["loss_if_flat"] = a["trade"].loss_if_flat
    return jsonable({
        "market": {"spot": m.spot, "expiry": m.expiry, "source": m.source, "as_of": m.as_of,
                   "market_open": is_market_hours(m.as_of)},
        **a, "trade": trade, "disclaimer": DISCLAIMER,
    })


@app.get("/api/scan")
def api_scan(option_type: str = Query(..., alias="type", pattern="^(call|put)$")):
    """Every strike within $6 of SPY, rated the same way as /api/check (premium = ask)."""
    m = market()
    strikes = sorted(k for k in m.chain.loc[m.chain["type"] == option_type, "strike"] if abs(k - m.spot) <= SCAN_WINDOW)
    rows = []
    for k in strikes:
        try:
            a = analyze(m, option_type, k, None, full=False)
        except ValueError:
            continue
        c, d, ev = a["trade"], a["decision"], a["evidence"]
        rows.append({
            "strike": k, "ask": c.ask, "cost": c.ask * 100, "breakeven": c.breakeven, "move_needed": c.move_needed,
            "prob_profit": ev["realistic_profit"] if ev else c.prob_profit, "realistic": ev is not None,
            "rating": d.get("rating"), "label": d.get("label"), "score": d.get("score"),
            "similar_return": ev["similar"]["return_per_dollar"] if ev and ev.get("similar") else None,
        })
    return jsonable({"type": option_type, "spot": m.spot, "as_of": m.as_of, "rows": rows,
                     "market_open": is_market_hours(m.as_of)})


@app.get("/api/spy")
def api_spy():
    """Today's session so far (or the last full session when closed), for the price chart."""
    m = market()
    try:
        bars = live_inputs()[0]
    except Exception as e:
        raise HTTPException(503, f"SPY bars unavailable: {e}") from e
    days = sorted(set(bars.index.date))
    day = m.as_of.date() if m.as_of.date() in days else days[-1]
    session = bars[bars.index.date == day]
    prev = bars[bars.index.date < day]
    live = is_market_hours(m.as_of) and day == m.as_of.date()
    em = expected_move(m.chain, m.spot, m.T, m.r) if live and m.expiry == day else None
    return jsonable({
        "date": day, "live": live, "expiry": m.expiry,
        "bars": [{"t": ts.strftime("%H:%M"), "o": r.open, "h": r.high, "l": r.low, "c": r.close}
                 for ts, r in session.iterrows()],
        "prev_close": float(prev["close"].iloc[-1]) if len(prev) else None,
        "last": m.spot if live else (float(session["close"].iloc[-1]) if len(session) else None),
        "now": m.as_of.astimezone(ET).strftime("%H:%M") if live else None,
        "expected": {"low": em["low"], "high": em["high"], "move": em["move"]} if em else None,
    })


@app.get("/api/strategies")
def api_strategies():
    m = market()
    em = expected_move(m.chain, m.spot, m.T, m.r)
    live = is_market_hours(m.as_of) and minutes_to_expiry(m.as_of, m.expiry) <= SESSION_MINUTES
    ml_vol, bars, notes = None, None, list(m.notes)
    if live:
        try:
            bars, vix_prev, vix1d_prev = live_inputs()
            feats = live_forecast.live_features(bars, vix_prev, m.as_of, vix1d_prev)
            if feats is not None and _model["bundle"] is not None:
                ml_vol = live_forecast.forecast(_model["bundle"], feats, m.spot)["vol"]
        except Exception as e:
            notes.append(f"ML forecast unavailable ({type(e).__name__}).")
    sig = strategy_signals.signals(m, em, ml_vol, bars, live)
    return jsonable({
        "market": {"spot": m.spot, "expiry": m.expiry, "as_of": m.as_of, "live": live,
                   "minutes_left": minutes_to_expiry(m.as_of, m.expiry)},
        "signals": sig,
        "lab": (_insights or {}).get("strategy_lab"),
        "notes": notes, "disclaimer": DISCLAIMER,
    })


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
