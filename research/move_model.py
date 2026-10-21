"""ML forecast of how much SPY will move between now and the 4 PM close.

Target: realized variance (RV) of SPY from time t to the close = sum of squared 5-minute log returns.
That's the quantity the options market prices: implied variance = IV² × T.

Features (all known at time t, nothing from the future):
  minutes_left      time to the close
  rv_so_far_pm      today's RV so far, per minute (how wild today has been)
  move_so_far       |log(S_t / today's open)|
  range_so_far      log(high / low) so far today
  gap               |log(today's open / yesterday's close)| (overnight jump)
  rv_1d, rv_5d, rv_22d   yesterday's / last week's / last month's average daily RV (the classic HAR-RV inputs)
  rv_tod            average RV from this time of day to the close over the previous 60 days (intraday seasonality)
  vix_prev          VIX at yesterday's close (the market's 30-day fear gauge)
  dow               day of week
The SAME function (features_at) builds training rows and the live row, so training and serving can't drift.

Models compared (walk-forward: train on earlier years only, test on the next year):
  seasonal  rv_tod scaled by recent volatility (simple rule)
  har       linear regression on logs (the standard econometrics baseline)
  gbm       gradient-boosted trees (scikit-learn HistGradientBoostingRegressor)
"""
from dataclasses import dataclass, field
from datetime import date, time
from math import log, sqrt

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression

from core.expiry import MINUTES_PER_YEAR

TRAIN_TIMES = [time(h, m) for h in range(10, 16) for m in (0, 30)]
FEATURES = ["minutes_left", "rv_so_far_pm", "move_so_far", "range_so_far", "gap",
            "rv_1d", "rv_5d", "rv_22d", "rv_tod", "vix_prev", "dow"]
LOG_FEATURES = ["rv_so_far_pm", "rv_1d", "rv_5d", "rv_22d", "rv_tod"]
TOD_LOOKBACK = 60
EPS = 1e-10


def _mins_left(t: time) -> int:
    return 16 * 60 - (t.hour * 60 + t.minute)


def bar_returns(day_bars: pd.DataFrame) -> np.ndarray:
    """5-minute log returns; the first bar's return is open -> close of that bar."""
    c = np.log(day_bars["close"].to_numpy())
    first = c[0] - log(day_bars["open"].iloc[0])
    return np.concatenate([[first], np.diff(c)])


@dataclass
class DayStats:
    """Per-day numbers that later days need as history."""
    open: float
    close: float
    rv: float                                              # full-day RV
    rv_from: dict = field(default_factory=dict)            # minutes_left -> RV from that time to the close


def day_stats(day_bars: pd.DataFrame, times=TRAIN_TIMES) -> DayStats:
    r = bar_returns(day_bars)
    starts = day_bars.index.time
    rv_from = {_mins_left(t): float(np.sum(r[starts >= t] ** 2)) for t in times}
    return DayStats(float(day_bars["open"].iloc[0]), float(day_bars["close"].iloc[-1]), float(np.sum(r ** 2)), rv_from)


def rv_from_time(day_bars: pd.DataFrame, t: time) -> float:
    r = bar_returns(day_bars)
    return float(np.sum(r[day_bars.index.time >= t] ** 2))


def features_at(today_bars: pd.DataFrame, t: time, past: list[DayStats], past_rv_tod: list[float],
                vix_prev: float, day: date) -> dict | None:
    """Feature row at time t. today_bars may include bars after t: only bars that have ENDED by t are used.
    past = DayStats for previous full sessions (oldest first); past_rv_tod = their RV from t to the close."""
    seen = today_bars[today_bars.index.time < t]
    if seen.empty or len(past) < 22 or not past_rv_tod:
        return None
    r = bar_returns(seen)
    mins = _mins_left(t)
    elapsed = 390 - mins
    S = float(seen["close"].iloc[-1])
    day_open = float(seen["open"].iloc[0])
    rvs = np.array([p.rv for p in past[-22:]])
    return {
        "minutes_left": mins,
        "rv_so_far_pm": float(np.sum(r ** 2)) / elapsed,
        "move_so_far": abs(log(S / day_open)),
        "range_so_far": log(seen["high"].max() / seen["low"].min()),
        "gap": abs(log(day_open / past[-1].close)),
        "rv_1d": rvs[-1], "rv_5d": float(rvs[-5:].mean()), "rv_22d": float(rvs.mean()),
        "rv_tod": float(np.mean(past_rv_tod[-TOD_LOOKBACK:])),
        "vix_prev": vix_prev / 100,
        "dow": day.weekday(),
        "spot": S,
    }


def build_dataset(spy: pd.DataFrame, vix: pd.Series, times=TRAIN_TIMES) -> pd.DataFrame:
    """One row per (day, time) with features + targets. spy: 5-min bars (index = bar start, ET)."""
    days = [(d, g) for d, g in spy.groupby(spy.index.date) if g.index[-1].time() >= time(15, 55)]
    vix = vix.sort_index()
    past: list[DayStats] = []
    rows = []
    for day, bars in days:
        prev_vix = vix[vix.index < day]
        if len(prev_vix):
            for t in times:
                mins = _mins_left(t)
                f = features_at(bars, t, past, [p.rv_from[mins] for p in past], float(prev_vix.iloc[-1]), day)
                if f is None:
                    continue
                close = float(bars["close"].iloc[-1])
                rows.append({"date": day, "time": t.strftime("%H:%M"), **f,
                             "rv_rest": rv_from_time(bars, t), "ret_rest": log(close / f["spot"])})
        past.append(day_stats(bars, times))
    return pd.DataFrame(rows)


# ---------------- models ----------------
def _X(df: pd.DataFrame) -> pd.DataFrame:
    X = df[FEATURES].copy()
    for c in LOG_FEATURES:
        X[c] = np.log(X[c] + EPS)
    X["minutes_left"] = np.log(X["minutes_left"])
    return X


def _y(df: pd.DataFrame) -> np.ndarray:
    return np.log(df["rv_rest"].to_numpy() + EPS)


class MoveModel:
    """Predicts RV to the close. kind: 'seasonal' | 'har' | 'gbm'."""

    def __init__(self, kind: str = "gbm", extra_features: tuple = ()):
        self.kind = kind
        self.extra = tuple(extra_features)   # optional inputs, e.g. ("vix1d_prev",); gbm handles missing values
        self.scale = 1.0      # bias correction: E[RV] vs exp(E[log RV])
        if kind == "gbm":
            self.est = HistGradientBoostingRegressor(max_iter=400, learning_rate=0.04, max_leaf_nodes=24,
                                                     min_samples_leaf=80, l2_regularization=1.0, random_state=0)
        elif kind == "har":
            self.est = LinearRegression()

    def _raw(self, df: pd.DataFrame) -> np.ndarray:
        if self.kind == "seasonal":
            # time-of-day RV, scaled by how volatile the last week was vs the last month
            return np.log(df["rv_tod"] * (df["rv_5d"] / df["rv_22d"]) + EPS).to_numpy()
        return self.est.predict(self._features(df))

    def fit(self, df: pd.DataFrame) -> "MoveModel":
        if self.kind != "seasonal":
            self.est.fit(self._features(df), _y(df))
        self.scale = float(df["rv_rest"].mean() / np.exp(self._raw(df)).mean())
        return self

    def _features(self, df: pd.DataFrame) -> pd.DataFrame:
        X = _X(df)
        if self.kind == "har":
            X = X.drop(columns=["dow"])
        for c in self.extra:
            X[c] = df[c].to_numpy() if c in df else np.nan
        return X

    def predict_rv(self, df: pd.DataFrame) -> np.ndarray:
        return np.exp(self._raw(df)) * self.scale

    def predict_vol(self, df: pd.DataFrame) -> np.ndarray:
        """Annualized volatility in the same convention as our implied vols (T = minutes / 525,600)."""
        T = df["minutes_left"].to_numpy() / MINUTES_PER_YEAR
        return np.sqrt(self.predict_rv(df) / T)


# ---------------- evaluation ----------------
def qlike(rv: np.ndarray, pred: np.ndarray) -> float:
    """Standard loss for variance forecasts (robust to noisy realized variance). Lower is better."""
    ratio = (rv + EPS) / pred
    return float(np.mean(ratio - np.log(ratio) - 1))


def score(rv: np.ndarray, ret: np.ndarray, pred_rv: np.ndarray) -> dict:
    return {
        "qlike": qlike(rv, pred_rv),
        "log_mae": float(np.mean(np.abs(np.log(rv + EPS) - np.log(pred_rv)))),
        "within_1sd": float(np.mean(np.abs(ret) <= np.sqrt(pred_rv))),   # ≈ 68% if well calibrated
        "n": int(len(rv)),
    }


def walk_forward(data: pd.DataFrame, kinds=("seasonal", "har", "gbm"), first_test_year: int = 2020,
                 variants: dict | None = None) -> pd.DataFrame:
    """For each year Y: train on everything before Y, predict Y. Returns out-of-sample predictions.
    variants: extra named models, e.g. {"gbm_vix1d": ("gbm", ("vix1d_prev",))}."""
    data = data.copy()
    years = sorted({d.year for d in data["date"]})
    out = []
    for y in [y for y in years if y >= first_test_year]:
        train = data[[d.year < y for d in data["date"]]]
        test = data[[d.year == y for d in data["date"]]].copy()
        for k in kinds:
            test[f"pred_{k}"] = MoveModel(k).fit(train).predict_rv(test)
        for name, (kind, extra) in (variants or {}).items():
            # an input that didn't exist yet in the training years (e.g. VIX1D before 2023) can't be learned
            usable = all(train[c].notna().any() for c in extra if c in train)
            test[f"pred_{name}"] = MoveModel(kind, extra).fit(train).predict_rv(test) if usable else np.nan
        out.append(test)
    return pd.concat(out, ignore_index=True)
