"""API tests with a synthetic market and history (no network)."""
from datetime import date, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app as server
from tests.test_trade_check import synthetic_market


@pytest.fixture
def client(monkeypatch):
    m = synthetic_market()
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(50)]
    daily = pd.DataFrame({"Open": 770.0, "Close": [770.0 * (1 + 0.004 * ((i % 5) - 2)) for i in range(50)]},
                         index=days)
    monkeypatch.setitem(server._cache, "market", None)              # empty cache -> loads our fake
    monkeypatch.setattr(server, "load_market", lambda: m)
    monkeypatch.setattr(server, "daily_bars", lambda as_of: daily)
    monkeypatch.setattr(server, "intraday_bars", lambda as_of: None)
    return TestClient(server.app)


def test_market(client):
    j = client.get("/api/market").json()
    assert j["spot"] == 770.0
    assert j["expected"]["move"] > 0
    assert {q["strike"] for q in j["quotes"]["call"]} >= {768.0, 772.0}
    assert "not financial advice" in j["disclaimer"]


def test_check_returns_all_six_outputs(client):
    j = client.get("/api/check", params={"type": "call", "strike": 772}).json()
    t = j["trade"]
    assert t["breakeven"] == pytest.approx(772 + t["ask"])          # 1
    assert t["expected"]["move"] > 0                                 # 2
    assert 0 < t["prob_itm"] < 1 and t["prob_profit"] < t["prob_itm"]  # 3
    assert t["theta_per_hour"] < 0 and t["loss_if_flat"] > 0         # 4
    assert j["history"]["basis"] == "daily" and j["history"]["daily"]["n_days"] == 50  # 5
    assert j["history"]["verdict"] in {"Cheap", "Fair", "Expensive"}  # 6
    curve = j["greeks_curve"]
    assert curve["minutes"][0] == 390 and curve["minutes"][-1] == 5
    assert len(curve["series"]) == 2 and len(curve["series"][1]["gamma"]) == len(curve["minutes"])


def test_premium_override(client):
    j = client.get("/api/check", params={"type": "put", "strike": 768, "premium": 0.5}).json()
    assert j["trade"]["breakeven"] == pytest.approx(767.5)


def test_bad_inputs(client):
    assert client.get("/api/check", params={"type": "straddle", "strike": 770}).status_code == 422
    r = client.get("/api/check", params={"type": "call", "strike": 900})
    assert r.status_code == 422 and "Available" in r.json()["detail"]
    assert client.get("/api/check", params={"type": "call", "strike": 772, "premium": -1}).status_code == 422


def test_history_failure_does_not_break_check(client, monkeypatch):
    def boom(as_of):
        raise RuntimeError("yahoo down")
    monkeypatch.setattr(server, "daily_bars", boom)
    j = client.get("/api/check", params={"type": "call", "strike": 772}).json()
    assert j["history"] is None
    assert any("History check unavailable" in n for n in j["notes"])
    assert j["trade"]["breakeven"] > 0


def test_frontend_served(client):
    page = client.get("/")
    assert "0DTE Trade Checker" in page.text
    assert page.headers["cache-control"] == "no-cache"
    assert client.get("/app.js").status_code == 200
    assert client.get("/research.html").status_code == 200


def test_check_includes_backtest_evidence(client, monkeypatch):
    monkeypatch.setattr(server, "_insights", {
        "similar": [], "options_range": ["2024-02-01", "2026-10-14"],
        "curves": {"itm": {"x": [0, 1], "y": [0, 0.9]}, "profit": {"x": [0, 1], "y": [0, 0.8]}},
        "model_verdicts": [], "all_trades": [{"return_per_dollar": -0.07}]})
    j = client.get("/api/check", params={"type": "call", "strike": 772}).json()
    ev = j["evidence"]
    assert ev["realistic_profit"] == pytest.approx(0.8 * j["trade"]["prob_profit"])
    assert ev["z"] > 0                                   # 772 call with SPY at 770 is out of the money


def test_check_without_insights(client, monkeypatch):
    monkeypatch.setattr(server, "_insights", None)
    assert client.get("/api/check", params={"type": "call", "strike": 772}).json()["evidence"] is None


def test_check_includes_decision(client):
    d = client.get("/api/check", params={"type": "call", "strike": 772}).json()["decision"]
    assert "reasons" in d and 1 <= len(d["reasons"]) <= 3
    assert d["z"] > 0
    if d["available"]:
        assert d["rating"] in {"avoid", "poor", "typical"} and d["label"]


def test_scan_rates_every_nearby_strike(client):
    j = client.get("/api/scan", params={"type": "put"}).json()
    strikes = [r["strike"] for r in j["rows"]]
    assert strikes == sorted(strikes) and all(abs(k - 770) <= 8 for k in strikes)
    r = j["rows"][0]
    assert r["cost"] == pytest.approx(r["ask"] * 100)
    assert r["breakeven"] == pytest.approx(r["strike"] - r["ask"])
    assert client.get("/api/scan", params={"type": "straddle"}).status_code == 422


def test_spy_chart_endpoint(client, monkeypatch):
    from tests.test_move_model import random_spy
    spy, _ = random_spy(30, seed=7)
    last_day = sorted(set(spy.index.date))[-1]
    monkeypatch.setattr(server, "live_inputs", lambda: (spy, 0.17, 0.15))
    j = client.get("/api/spy").json()
    assert j["date"] == str(last_day)                       # market snapshot is a different day -> last session
    assert len(j["bars"]) == 78 and j["bars"][0]["t"] == "09:30"
    assert j["prev_close"] == pytest.approx(float(spy[spy.index.date < last_day]["close"].iloc[-1]))
    assert j["live"] is False and j["expected"] is None


def test_spy_chart_endpoint_unavailable(client, monkeypatch):
    def boom():
        raise RuntimeError("yahoo down")
    monkeypatch.setattr(server, "live_inputs", boom)
    assert client.get("/api/spy").status_code == 503
