"""Live ML forecast: the model's expected move from now to the close, for the web app.

Uses the SAME features_at() as training. Live bars come from yfinance (last ~60 days of
5-minute bars, including today's so far); VIX from yfinance's daily history.
"""
from datetime import date, datetime, time, timedelta
from math import pi, sqrt
from pathlib import Path

import joblib
import pandas as pd

from core.expiry import ET, MINUTES_PER_YEAR
from research.move_model import day_stats, features_at, rv_from_time

MODEL_FILE = Path(__file__).resolve().parents[1] / "models" / "move_model.joblib"


def load_bundle(path: Path = MODEL_FILE) -> dict | None:
    return joblib.load(path) if path.exists() else None


def decision_time(now: datetime) -> time | None:
    """Round down to the 5-minute grid; None outside 9:35–15:55 (too early for features / too late to matter)."""
    now = now.astimezone(ET)
    t = time(now.hour, now.minute - now.minute % 5)
    return t if time(9, 35) <= t <= time(15, 55) else None


def live_features(bars: pd.DataFrame, vix_prev: float, now: datetime) -> dict | None:
    """bars: 5-min bars (index = bar start, ET; columns open/high/low/close) for recent days incl. today."""
    t = decision_time(now)
    if t is None:
        return None
    today = now.astimezone(ET).date()
    past_groups = [g for d, g in bars.groupby(bars.index.date) if d < today and g.index[-1].time() >= time(15, 55)]
    today_bars = bars[bars.index.date == today]
    past = [day_stats(g, times=[]) for g in past_groups]
    return features_at(today_bars, t, past, [rv_from_time(g, t) for g in past_groups], vix_prev, today)


def forecast(bundle: dict, features: dict, spot: float) -> dict:
    """Model's volatility and expected move to the close (same units as the market's numbers)."""
    row = pd.DataFrame([features])
    model = bundle["model"]
    rv = float(model.predict_rv(row)[0])
    T = features["minutes_left"] / MINUTES_PER_YEAR
    return {
        "vol": sqrt(rv / T),
        "expected_move": sqrt(2 / pi) * spot * sqrt(rv),
        "one_sd_move": spot * sqrt(rv),
        "minutes_left": features["minutes_left"],
        "trained_through": bundle.get("trained_through"),
    }


def fetch_live_inputs(symbol: str = "SPY") -> tuple[pd.DataFrame, float]:
    """Recent 5-min bars (incl. today) and VIX at the previous close, from yfinance."""
    import yfinance as yf

    raw = yf.Ticker(symbol).history(period="60d", interval="5m")
    bars = raw.rename(columns=str.lower)[["open", "high", "low", "close"]]
    bars.index = bars.index.tz_convert(ET)
    t = bars.index.time
    bars = bars[(t >= time(9, 30)) & (t < time(16, 0))]

    vix = yf.Ticker("^VIX").history(period="10d", interval="1d")["Close"]
    vix.index = vix.index.tz_convert(ET).date
    today = datetime.now(ET).date()
    vix_prev = float(vix[vix.index < today].iloc[-1])
    return bars, vix_prev
