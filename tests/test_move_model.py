"""ML move model on synthetic price paths (no network)."""
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from core.expiry import ET
from research.move_model import (FEATURES, MoveModel, bar_returns, build_dataset, day_stats, features_at,
                                 qlike, rv_from_time, score, walk_forward)


def session(day, closes, opens=None):
    idx = pd.DatetimeIndex([datetime.combine(day, time(9, 30), tzinfo=ET) + timedelta(minutes=5 * i) for i in range(78)])
    closes = np.asarray(closes, dtype=float)
    opens = closes if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes), "low": np.minimum(opens, closes),
                         "close": closes, "volume": 1}, index=idx)


def random_spy(n_days=80, seed=0):
    rng = np.random.default_rng(seed)
    frames, price, day = [], 100.0, date(2020, 1, 1)
    vols = []
    while len(frames) < n_days:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        vol = rng.choice([0.0005, 0.001, 0.002])          # per-bar vol regime for the day
        vols.append(vol)
        rets = rng.normal(0, vol, 78)
        closes = price * np.exp(np.cumsum(rets))
        opens = np.concatenate([[price], closes[:-1]])
        frames.append(session(day, closes, opens))
        price = closes[-1]
    spy = pd.concat(frames)
    vix = pd.Series(20.0, index=sorted({d - timedelta(days=1) for d in spy.index.date} | set(spy.index.date)))
    return spy, vix


def test_bar_returns():
    bars = session(date(2026, 1, 5), [101.0] + [101.0] * 77, opens=[100.0] + [101.0] * 77)
    r = bar_returns(bars)
    assert r[0] == pytest.approx(np.log(1.01))
    assert np.allclose(r[1:], 0)


def test_rv_from_time():
    closes = [100.0] * 60 + [101.0] + [101.0] * 17          # one jump in the bar starting at 14:30
    bars = session(date(2026, 1, 5), closes)
    assert rv_from_time(bars, time(14, 0)) == pytest.approx(np.log(1.01) ** 2)
    assert rv_from_time(bars, time(15, 0)) == pytest.approx(0)


def test_features_ignore_the_future():
    spy, vix = random_spy(30)
    days = sorted(set(spy.index.date))
    past = [day_stats(spy[spy.index.date == d]) for d in days[:-1]]
    today = spy[spy.index.date == days[-1]].copy()
    f1 = features_at(today, time(12, 0), past, [p.rv_from[240] for p in past], 20.0, days[-1])
    spiked = today.copy()
    spiked.loc[spiked.index.time >= time(12, 0), ["close", "high"]] *= 1.5     # crash/rally AFTER noon
    f2 = features_at(spiked, time(12, 0), past, [p.rv_from[240] for p in past], 20.0, days[-1])
    assert f1 == f2


def test_needs_a_month_of_history():
    spy, _ = random_spy(10)
    days = sorted(set(spy.index.date))
    past = [day_stats(spy[spy.index.date == d]) for d in days[:-1]]
    assert features_at(spy[spy.index.date == days[-1]], time(12, 0), past, [1e-5], 20.0, days[-1]) is None


def test_build_dataset_targets():
    spy, vix = random_spy(40)
    data = build_dataset(spy, vix)
    assert set(FEATURES) <= set(data.columns)
    row = data.iloc[-1]
    bars = spy[spy.index.date == row["date"]]
    t = time.fromisoformat(row["time"])
    assert row["rv_rest"] == pytest.approx(rv_from_time(bars, t))
    assert row["ret_rest"] == pytest.approx(np.log(bars["close"].iloc[-1] / row["spot"]))
    assert data["date"].min() > sorted(set(spy.index.date))[21]       # first 22 days are warm-up only


def test_qlike_and_score():
    rv = np.array([1e-5, 2e-5])
    assert qlike(rv, rv) == pytest.approx(0, abs=1e-9)
    assert qlike(rv, rv * 2) > 0
    s = score(rv, np.array([0.001, 0.01]), rv)
    assert s["within_1sd"] == 0.5 and s["n"] == 2


@pytest.mark.parametrize("kind", ["seasonal", "har", "gbm"])
def test_models_fit_and_are_unbiased_in_sample(kind):
    spy, vix = random_spy(120, seed=1)
    data = build_dataset(spy, vix)
    m = MoveModel(kind).fit(data)
    pred = m.predict_rv(data)
    assert np.all(pred > 0)
    assert pred.mean() == pytest.approx(data["rv_rest"].mean(), rel=1e-6)    # the scale fixes the bias
    vol = m.predict_vol(data.head(3))
    assert np.all((vol > 0.01) & (vol < 5))


def test_today_vol_regime_is_learned():
    # Each day's vol is constant within the day, so "RV so far" should beat the seasonal rule
    spy, vix = random_spy(260, seed=2)
    data = build_dataset(spy, vix)
    cut = data["date"].iloc[len(data) * 2 // 3]
    train, test = data[data["date"] < cut], data[data["date"] >= cut]
    q_seasonal = qlike(test["rv_rest"].to_numpy(), MoveModel("seasonal").fit(train).predict_rv(test))
    q_har = qlike(test["rv_rest"].to_numpy(), MoveModel("har").fit(train).predict_rv(test))
    assert q_har < q_seasonal


def test_walk_forward_is_out_of_sample():
    spy, vix = random_spy(300, seed=3)
    data = build_dataset(spy, vix)
    years = sorted({d.year for d in data["date"]})
    wf = walk_forward(data, kinds=("seasonal", "har"), first_test_year=years[1])
    assert {d.year for d in wf["date"]} == set(years[1:])
    assert {"pred_seasonal", "pred_har"} <= set(wf.columns)
