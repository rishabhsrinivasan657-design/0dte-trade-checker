"""Tests for core/implied_vol.py. Main idea: price with a known sigma, solve back, get sigma again."""
from math import isnan

import pytest

from core.black_scholes import bs_price
from core.implied_vol import implied_vol, no_arbitrage_bounds

MIN_PER_YEAR = 525_600


def test_hull_round_trip():
    price = bs_price(42, 40, 0.5, 0.10, 0.20, "call")
    assert implied_vol(price, 42, 40, 0.5, 0.10, "call") == pytest.approx(0.20, abs=1e-8)


@pytest.mark.parametrize("minutes", [390, 60, 15])
@pytest.mark.parametrize("K", [770.0, 776.0, 778.0, 780.0, 785.0])
@pytest.mark.parametrize("sigma", [0.08, 0.13, 0.40])
@pytest.mark.parametrize("cp", ["call", "put"])
def test_0dte_round_trip(minutes, K, sigma, cp):
    S, T, r = 778.0, minutes / MIN_PER_YEAR, 0.04
    price = bs_price(S, K, T, r, sigma, cp)
    lower, _ = no_arbitrage_bounds(S, K, T, r, cp)
    if price - lower < 1e-4:
        pytest.skip("deep ITM / far OTM: under $0.0001 of time value, sigma not identifiable")
    iv = implied_vol(price, S, K, T, r, cp)
    assert bs_price(S, K, T, r, iv, cp) == pytest.approx(price, abs=1e-9)
    assert iv == pytest.approx(sigma, rel=1e-4)


def test_tiny_vega_far_otm_last_minutes():
    # 5 minutes left, $3 OTM: vega is almost zero, so a pure Newton step would blow up
    S, K, T, r = 778.0, 781.0, 5 / MIN_PER_YEAR, 0.04
    price = bs_price(S, K, T, r, 0.30, "call")
    iv = implied_vol(price, S, K, T, r, "call")
    assert iv == pytest.approx(0.30, rel=1e-3)


def test_bad_starting_guess_high_vol():
    price = bs_price(778, 778, 60 / MIN_PER_YEAR, 0.04, 3.0, "call")
    assert implied_vol(price, 778, 778, 60 / MIN_PER_YEAR, 0.04, "call") == pytest.approx(3.0, rel=1e-6)


@pytest.mark.parametrize("price", [1.99, 2.0])   # 780 call vs 778 strike: intrinsic is about $2
def test_price_at_or_below_intrinsic_is_nan(price):
    assert isnan(implied_vol(price, 780, 778, 60 / MIN_PER_YEAR, 0.0, "call"))


def test_price_above_max_is_nan():
    assert isnan(implied_vol(800.0, 778, 778, 0.01, 0.04, "call"))   # a call can't cost more than the stock


def test_expired_is_nan():
    assert isnan(implied_vol(1.0, 778, 778, 0.0, 0.04, "call"))
