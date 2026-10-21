"""Live Strategy Lab signals on a synthetic chain (no network)."""
from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest

from checker import strategy_signals as ss
from core.expiry import ET
from data.chain import expected_move
from tests.test_move_model import random_spy
from tests.test_trade_check import synthetic_market


@pytest.fixture(scope="module")
def market():
    return synthetic_market(strikes=range(755, 786))


def test_condor_fills_at_bid_and_ask(market):
    em = expected_move(market.chain, market.spot, market.T, market.r)
    c = ss.condor_setup(market.chain, market.spot, market.T, market.r, em["atm_iv"], em["straddle"])
    sells = [l for l in c["legs"] if l["action"] == "sell"]
    buys = [l for l in c["legs"] if l["action"] == "buy"]
    chain = market.chain.set_index(["type", "strike"])
    assert all(l["price"] == pytest.approx(chain.loc[(l["type"], l["strike"]), "bid"]) for l in sells)
    assert all(l["price"] == pytest.approx(chain.loc[(l["type"], l["strike"]), "ask"]) for l in buys)
    kp, kc = c["short_strikes"]
    assert kp < market.spot < kc and c["width"] == 2.0
    assert c["max_loss"] == pytest.approx(2.0 - c["credit"])
    assert c["breakevens"] == pytest.approx([kp - c["credit"], kc + c["credit"]])
    assert 0 < c["chance_max_profit"] < c["chance_profit"] < 1


def test_breakeven_condor_targets_12_delta(market):
    em = expected_move(market.chain, market.spot, market.T, market.r)
    c = ss.breakeven_setup(market.chain, market.spot, market.T, market.r, em["atm_iv"])
    assert c["width"] == 3.0 and "whole credit" in c["stop"]
    kp, kc = c["short_strikes"]
    assert kp < market.spot < kc


def test_ml_gate(market):
    em = expected_move(market.chain, market.spot, market.T, market.r)
    sell = ss.signals(market, em, em["atm_iv"] * 0.8, None, live=True)
    hold = ss.signals(market, em, em["atm_iv"] * 1.2, None, live=True)
    none = ss.signals(market, em, None, None, live=True)
    assert sell["gate"] == "sell" and sell["ml_ratio"] == pytest.approx(0.8)
    assert hold["gate"] == "hold off" and none["gate"] is None


def test_momentum_signal_timing_and_direction():
    spy, _ = random_spy(70, seed=8)
    last = sorted(set(spy.index.date))[-1]
    at = lambda h, m: datetime.combine(last, time(h, m), tzinfo=ET)
    assert ss.momentum_signal(spy, at(9, 45))["state"] == "waiting"
    s = ss.momentum_signal(spy, at(12, 0))
    prev_close = float(spy[spy.index.date < last]["close"].iloc[-1])
    ten = float(spy[(spy.index.date == last) & (spy.index.time < time(10, 0))]["close"].iloc[-1])
    assert s["r_first"] == pytest.approx(ten / prev_close - 1)
    assert s["direction"] == ("call" if ten > prev_close else "put") and s["window"] == "later"
    assert ss.momentum_signal(spy, at(15, 40))["window"] == "open"
