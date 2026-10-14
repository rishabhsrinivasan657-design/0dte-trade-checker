"""Trade checker on a synthetic market where every price comes from Black-Scholes at a known vol."""
from datetime import date, datetime, time

import pandas as pd
import pytest

from checker.trade_check import HOURS_PER_YEAR, check_trade
from core.black_scholes import bs_price, prob_itm
from core.expiry import ET
from data.chain import MarketSnapshot, clean_chain

S, R, SIGMA, MINUTES = 770.0, 0.04, 0.18, 200
T = MINUTES / 525_600
EXP = date(2026, 10, 14)
HALF_SPREAD = 0.005


def synthetic_market(strikes=range(760, 781), sigma=SIGMA, minutes=MINUTES) -> MarketSnapshot:
    t = minutes / 525_600
    rows = []
    for k in strikes:
        for cp in ("call", "put"):
            fair = bs_price(S, k, t, R, sigma, cp)
            rows.append({"symbol": f"{cp}{k}", "expiry": EXP, "type": cp, "strike": float(k),
                         "bid": round(fair - HALF_SPREAD, 6), "ask": round(fair + HALF_SPREAD, 6),
                         "quote_time": None, "vendor_iv": None})
    chain = clean_chain(pd.DataFrame(rows))
    return MarketSnapshot(spot=S, quoted_spot=S, expiry=EXP, T=t, r=R, chain=chain, source="test",
                          as_of=datetime.combine(EXP, time(12, 40), tzinfo=ET))


@pytest.fixture(scope="module")
def market():
    return synthetic_market()


def test_premium_defaults_to_ask_and_breakeven(market):
    c = check_trade(market, "call", 772)
    assert c.premium == pytest.approx(c.ask)
    assert c.breakeven == pytest.approx(772 + c.ask)
    p = check_trade(market, "put", 768)
    assert p.breakeven == pytest.approx(768 - p.ask)
    assert p.move_needed < 0                     # a put needs SPY to fall


def test_premium_override(market):
    c = check_trade(market, "call", 772, premium=0.40)
    assert c.premium == 0.40
    assert c.breakeven == pytest.approx(772.40)


def test_iv_is_recovered_from_mid(market):
    assert check_trade(market, "call", 772).iv == pytest.approx(SIGMA, rel=1e-6)
    assert check_trade(market, "put", 768).iv == pytest.approx(SIGMA, rel=1e-6)


def test_probabilities(market):
    c = check_trade(market, "call", 772)
    assert c.prob_itm == pytest.approx(prob_itm(S, 772, T, R, SIGMA, "call"), rel=1e-6)
    assert c.prob_profit < c.prob_itm            # must clear strike + premium, not just strike
    assert c.prob_profit == pytest.approx(prob_itm(S, c.breakeven, T, R, SIGMA, "call"), rel=1e-6)


def test_move_vs_expected(market):
    c = check_trade(market, "call", 772)
    assert c.move_needed == pytest.approx(c.breakeven - S)
    assert c.move_vs_expected == pytest.approx(abs(c.move_needed) / c.expected["move"])


def test_time_decay(market):
    c = check_trade(market, "call", 770)
    assert c.theta_per_hour < 0
    assert c.hours_ahead == 1.0
    assert c.value_later == pytest.approx(bs_price(S, 770, T - 1 / HOURS_PER_YEAR, R, SIGMA, "call"), rel=1e-6)
    assert 0 < c.loss_if_flat < c.model_value


def test_last_minutes_look_ahead_stops_at_close():
    m = synthetic_market(minutes=20)
    c = check_trade(m, "call", 770)              # ATM: still has value with 20 min left
    assert c.hours_ahead == pytest.approx(20 / 60)
    assert c.value_later == pytest.approx(0.0)   # SPY unchanged = exactly at the strike = worth $0 at the close
    assert c.loss_if_flat == pytest.approx(c.model_value)


def test_deep_itm_falls_back_to_atm_iv():
    # 750 call with SPY at 770 and 200 min left: essentially pure intrinsic, IV unsolvable
    m = synthetic_market(strikes=[750, *range(765, 776)])
    c = check_trade(m, "call", 750)
    assert c.iv == pytest.approx(SIGMA, rel=1e-4)
    assert any("ATM IV" in n for n in c.notes)


def test_unknown_strike_raises(market):
    with pytest.raises(ValueError, match="Available"):
        check_trade(market, "call", 900)


def test_bad_premium(market):
    with pytest.raises(ValueError):
        check_trade(market, "call", 772, premium=0)
