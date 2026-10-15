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
    assert "0DTE Trade Checker" in client.get("/").text
    assert client.get("/app.js").status_code == 200
