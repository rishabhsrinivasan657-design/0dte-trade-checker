"""Trade score: an ML model that rates a specific 0DTE purchase against all past ones.

Trained on the backtest (every call/put within $5 of SPY, bought on the hour, held to 4 PM).
Target: return per $1 paid, clipped to [-100%, +400%] so a handful of 50× days can't dominate.
Features (all known at the moment of the decision, same definitions live and in the backtest):
  is_call        call (1) or put (0)
  z              distance out of the money in typical moves (negative = in the money)
  minutes_left   time to the close
  iv_rel         this strike's implied vol / ATM implied vol (smile: is this strike priced richer?)
  atm_iv         overall implied vol level
  prob_itm       market's chance it finishes in the money (Black-Scholes N(d2))
  hist_edge      history hit rate − market probability (the old Cheap/Expensive signal, as a number)
  ml_ratio       ML-forecast volatility to the close / market's implied volatility
  vix_prev       VIX at yesterday's close
  move_so_far    |SPY move since today's open|
  log_price      log of the option's price (cheap lottery tickets vs expensive options)
Evaluated walk-forward: each month is scored by a model trained only on earlier months.
"""
from math import log

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

FEATURES = ["is_call", "z", "minutes_left", "iv_rel", "atm_iv", "prob_itm", "hist_edge",
            "ml_ratio", "vix_prev", "move_so_far", "log_price"]
CLIP = (-1.0, 4.0)
MIN_TRAIN_MONTHS = 6


def make_features(trades: pd.DataFrame) -> pd.DataFrame:
    """trades needs: type, z, minutes_left, iv, atm_iv, prob_itm, hist_intraday, ml_ratio, vix_prev,
    move_so_far, price. Missing inputs may be NaN (the model handles them)."""
    return pd.DataFrame({
        "is_call": (trades["type"] == "call").astype(float),
        "z": trades["z"],
        "minutes_left": trades["minutes_left"].astype(float),
        "iv_rel": trades["iv"] / trades["atm_iv"],
        "atm_iv": trades["atm_iv"],
        "prob_itm": trades["prob_itm"],
        "hist_edge": trades["hist_intraday"] - trades["prob_itm"],
        "ml_ratio": trades["ml_ratio"],
        "vix_prev": trades["vix_prev"],
        "move_so_far": trades["move_so_far"],
        "log_price": np.log(trades["price"].clip(lower=0.01)),
    }, index=trades.index)[FEATURES]


def target(trades: pd.DataFrame) -> np.ndarray:
    return trades["ret"].clip(*CLIP).to_numpy()


def new_model() -> HistGradientBoostingRegressor:
    # shallow + heavily regularized: the signal is weak and the data is noisy
    return HistGradientBoostingRegressor(max_iter=200, learning_rate=0.04, max_leaf_nodes=12,
                                         min_samples_leaf=400, l2_regularization=5.0, random_state=0)


def walk_forward(trades: pd.DataFrame) -> pd.Series:
    """Out-of-sample predicted return for every trade after the first MIN_TRAIN_MONTHS months."""
    month = pd.to_datetime(trades["date"]).dt.to_period("M")
    months = sorted(month.unique())
    X, y = make_features(trades), target(trades)
    pred = pd.Series(np.nan, index=trades.index)
    for m in months[MIN_TRAIN_MONTHS:]:
        train, test = (month < m).to_numpy(), (month == m).to_numpy()
        pred[test] = new_model().fit(X[train], y[train]).predict(X[test])
    return pred


def live_row(option_type: str, z: float, minutes_left: float, iv: float, atm_iv: float, prob_itm: float,
             hist_intraday: float | None, ml_ratio: float | None, vix_prev: float | None,
             move_so_far: float | None, price: float) -> pd.DataFrame:
    nan = float("nan")
    return make_features(pd.DataFrame([{
        "type": option_type, "z": z, "minutes_left": minutes_left, "iv": iv, "atm_iv": atm_iv,
        "prob_itm": prob_itm, "hist_intraday": nan if hist_intraday is None else hist_intraday,
        "ml_ratio": nan if ml_ratio is None else ml_ratio, "vix_prev": nan if vix_prev is None else vix_prev,
        "move_so_far": nan if move_so_far is None else move_so_far, "price": price,
    }]))


# ---------------- research pipeline helpers ----------------
RATINGS = [("avoid", "Avoid", 0.0, 0.2), ("poor", "Poor odds", 0.2, 0.4), ("typical", "Typical 0DTE trade", 0.4, 1.0)]


def add_context(trades: pd.DataFrame, move_wf: pd.DataFrame, atm: pd.DataFrame) -> pd.DataFrame:
    """Attach the inputs that come from other models: ML move forecast ratio, VIX, move so far.
    move_wf: walk-forward ML-move predictions (date, time, pred_gbm, vix_prev, move_so_far);
    atm: (date, time, implied_rv). Uses only out-of-sample move forecasts, so nothing leaks."""
    ctx = move_wf[["date", "time", "pred_gbm", "vix_prev", "move_so_far"]].merge(
        atm[["date", "time", "implied_rv"]], on=["date", "time"])
    ctx["ml_ratio"] = np.sqrt(ctx["pred_gbm"] / ctx["implied_rv"])
    return trades.merge(ctx[["date", "time", "ml_ratio", "vix_prev", "move_so_far"]], on=["date", "time"], how="left")


def rating_for(percentile: float) -> tuple[str, str]:
    for key, label, lo, hi in RATINGS:
        if lo <= percentile < hi or (hi == 1.0 and percentile >= lo):
            return key, label
    return RATINGS[-1][0], RATINGS[-1][1]


def rate_oos(scored: pd.DataFrame) -> pd.DataFrame:
    """Rating group for each out-of-sample prediction, by its percentile among all OOS predictions."""
    pct = scored["pred"].rank(pct=True)
    return scored.assign(rating=[rating_for(p)[0] for p in pct])
