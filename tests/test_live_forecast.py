"""Live ML forecast path (no network): same features as training, sane outputs, API wiring."""
from datetime import datetime, time
from math import pi, sqrt

import pytest
from fastapi.testclient import TestClient

from core.expiry import ET
from research import live_forecast as lf
from research.move_model import MoveModel, build_dataset, features_at, day_stats, rv_from_time
from tests.test_move_model import random_spy


def test_decision_time_grid():
    at = lambda h, m: datetime(2026, 10, 15, h, m, tzinfo=ET)
    assert lf.decision_time(at(13, 37)) == time(13, 35)
    assert lf.decision_time(at(9, 31)) is None          # too early: no full bar yet
    assert lf.decision_time(at(16, 1)) is None


def test_live_features_match_training_features():
    spy, vix = random_spy(40, seed=4)
    days = sorted(set(spy.index.date))
    today = days[-1]
    now = datetime.combine(today, time(13, 0), tzinfo=ET)
    live = lf.live_features(spy, 20.0, now)
    groups = [spy[spy.index.date == d] for d in days[:-1]]
    train_style = features_at(spy[spy.index.date == today], time(13, 0), [day_stats(g) for g in groups],
                              [rv_from_time(g, time(13, 0)) for g in groups], 20.0, today)
    assert live == pytest.approx(train_style)


def test_forecast_units():
    spy, vix = random_spy(120, seed=5)
    model = MoveModel("har").fit(build_dataset(spy, vix))
    bundle = {"model": model, "trained_through": "2020-06-01"}
    feats = lf.live_features(spy, 20.0, datetime.combine(sorted(set(spy.index.date))[-1], time(12, 0), tzinfo=ET))
    f = lf.forecast(bundle, feats, spot=100.0)
    T = feats["minutes_left"] / 525_600
    assert f["one_sd_move"] == pytest.approx(100 * f["vol"] * sqrt(T))
    assert f["expected_move"] == pytest.approx(sqrt(2 / pi) * f["one_sd_move"])
    assert f["trained_through"] == "2020-06-01"


def test_api_includes_model_during_session(monkeypatch):
    import app as server
    from tests.test_trade_check import synthetic_market

    spy, vix = random_spy(120, seed=6)
    bundle = {"model": MoveModel("har").fit(build_dataset(spy, vix)), "trained_through": "2020-06-01"}
    last = sorted(set(spy.index.date))[-1]
    m = synthetic_market()
    m.as_of = datetime.combine(last, time(12, 40), tzinfo=ET)       # a weekday, mid-session
    monkeypatch.setitem(server._cache, "market", None)
    monkeypatch.setattr(server, "load_market", lambda: m)
    monkeypatch.setattr(server, "daily_bars", lambda d: None)
    monkeypatch.setattr(server, "intraday_bars", lambda d: None)
    monkeypatch.setitem(server._model, "bundle", bundle)
    monkeypatch.setitem(server._model, "inputs", (spy, 20.0))
    monkeypatch.setitem(server._model, "at", float("inf"))           # "fetched in the future" -> never refetch

    j = TestClient(server.app).get("/api/check", params={"type": "call", "strike": 772}).json()
    mdl = j["model"]
    assert mdl is not None
    assert 0 < mdl["prob_itm"] < 1 and mdl["prob_profit"] <= mdl["prob_itm"]
    assert mdl["verdict"] in {"Cheap", "Fair", "Expensive"}
    assert mdl["market_move"] == pytest.approx(j["trade"]["expected"]["move"])


def test_api_model_absent_without_bundle(monkeypatch):
    import app as server
    from tests.test_trade_check import synthetic_market
    monkeypatch.setitem(server._cache, "market", None)
    monkeypatch.setattr(server, "load_market", lambda: synthetic_market())
    monkeypatch.setattr(server, "daily_bars", lambda d: None)
    monkeypatch.setattr(server, "intraday_bars", lambda d: None)
    monkeypatch.setitem(server._model, "bundle", None)
    j = TestClient(server.app).get("/api/check", params={"type": "call", "strike": 772}).json()
    assert j["model"] is None
