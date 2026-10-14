"""Tests for core/black_scholes.py and core/greeks.py.

Reference values: Hull, "Options, Futures, and Other Derivatives", Example 15.6
(S=42, K=40, r=10%, sigma=20%, T=0.5). Greeks are checked against finite
differences of bs_price, so they test consistency, not memorised numbers.
"""
from math import exp

import pytest

from core.black_scholes import bs_price, d1_d2, prob_itm
from core.greeks import delta, gamma, theta, vega

HULL = dict(S=42.0, K=40.0, T=0.5, r=0.10, sigma=0.20)
MIN_PER_YEAR = 525_600


# ---------- price ----------
def test_d1_d2_hull():
    d1, d2 = d1_d2(**HULL)
    assert d1 == pytest.approx(0.7693, abs=1e-4)
    assert d2 == pytest.approx(0.6278, abs=1e-4)


def test_price_hull():
    assert bs_price(**HULL, option_type="call") == pytest.approx(4.76, abs=0.005)
    assert bs_price(**HULL, option_type="put") == pytest.approx(0.81, abs=0.005)


@pytest.mark.parametrize("S", [700.0, 778.0, 850.0])
def test_put_call_parity(S):
    # C - P = S - K e^{-rT}
    p = dict(S=S, K=778.0, T=0.01, r=0.04, sigma=0.15)
    c, put = bs_price(**p, option_type="call"), bs_price(**p, option_type="put")
    assert c - put == pytest.approx(S - 778.0 * exp(-0.04 * 0.01), abs=1e-8)


def test_0dte_atm_rule_of_thumb():
    # Full 6.5h session, SPY 778, 13% vol -> about $1.10 (0.4 * S * sigma * sqrt(T))
    T = 390 / MIN_PER_YEAR
    assert bs_price(778, 778, T, 0.04, 0.13, "call") == pytest.approx(1.10, rel=0.03)


@pytest.mark.parametrize("T,sigma", [(0.0, 0.13), (-1e-6, 0.13), (0.01, 0.0)])
def test_price_edge_cases_return_intrinsic(T, sigma):
    assert bs_price(780, 778, T, 0.04, sigma, "call") == pytest.approx(2.0)
    assert bs_price(780, 778, T, 0.04, sigma, "put") == pytest.approx(0.0)
    assert bs_price(775, 778, T, 0.04, sigma, "put") == pytest.approx(3.0)


def test_bad_option_type():
    with pytest.raises(ValueError):
        bs_price(**HULL, option_type="straddle")


def test_one_minute_left_is_finite():
    T = 1 / MIN_PER_YEAR
    price = bs_price(778, 800, T, 0.04, 0.13, "call")  # deep OTM, 1 minute left
    assert 0.0 <= price < 1e-6


# ---------- probability ITM ----------
def test_prob_itm_hull():
    assert prob_itm(**HULL, option_type="call") == pytest.approx(0.7349, abs=1e-4)


def test_prob_itm_call_plus_put_is_one():
    p = dict(S=778, K=780, T=200 / MIN_PER_YEAR, r=0.04, sigma=0.13)
    assert prob_itm(**p, option_type="call") + prob_itm(**p, option_type="put") == pytest.approx(1.0)


def test_prob_itm_at_expiry():
    assert prob_itm(780, 778, 0.0, 0.04, 0.13, "call") == 1.0
    assert prob_itm(780, 778, 0.0, 0.04, 0.13, "put") == 0.0


# ---------- Greeks vs finite differences ----------
GREEK_CASES = [
    dict(S=42.0, K=40.0, T=0.5, r=0.10, sigma=0.20),               # Hull
    dict(S=778.0, K=778.0, T=390 / MIN_PER_YEAR, r=0.04, sigma=0.13),  # 0DTE ATM, open
    dict(S=778.0, K=781.0, T=60 / MIN_PER_YEAR, r=0.04, sigma=0.13),   # 0DTE OTM, last hour
]


@pytest.mark.parametrize("p", GREEK_CASES)
@pytest.mark.parametrize("cp", ["call", "put"])
def test_delta_matches_finite_difference(p, cp):
    h = p["S"] * 1e-5
    up = bs_price(**{**p, "S": p["S"] + h}, option_type=cp)
    dn = bs_price(**{**p, "S": p["S"] - h}, option_type=cp)
    assert delta(**p, option_type=cp) == pytest.approx((up - dn) / (2 * h), rel=1e-4, abs=1e-6)


@pytest.mark.parametrize("p", GREEK_CASES)
def test_gamma_matches_finite_difference(p):
    # small step: at 0DTE gamma changes so fast that a coarse step (1e-4 * S) mis-measures it
    h = p["S"] * 1e-6
    up = bs_price(**{**p, "S": p["S"] + h})
    mid = bs_price(**p)
    dn = bs_price(**{**p, "S": p["S"] - h})
    assert gamma(**p) == pytest.approx((up - 2 * mid + dn) / h**2, rel=1e-3, abs=1e-6)


@pytest.mark.parametrize("p", GREEK_CASES)
def test_vega_matches_finite_difference(p):
    h = 1e-5
    up = bs_price(**{**p, "sigma": p["sigma"] + h})
    dn = bs_price(**{**p, "sigma": p["sigma"] - h})
    assert vega(**p) == pytest.approx((up - dn) / (2 * h), rel=1e-4, abs=1e-6)


@pytest.mark.parametrize("p", GREEK_CASES)
@pytest.mark.parametrize("cp", ["call", "put"])
def test_theta_matches_finite_difference(p, cp):
    # theta = dV/dt = -dV/dT  (value as time passes = T shrinks)
    h = p["T"] * 1e-4
    later = bs_price(**{**p, "T": p["T"] - h}, option_type=cp)
    now = bs_price(**{**p, "T": p["T"] + h}, option_type=cp)
    assert theta(**p, option_type=cp) == pytest.approx((later - now) / (2 * h), rel=1e-3)


def test_long_option_theta_negative_0dte():
    p = GREEK_CASES[1]
    assert theta(**p, option_type="call") < 0
    assert theta(**p, option_type="put") < 0


def test_greeks_at_expiry():
    assert delta(780, 778, 0.0, 0.04, 0.13, "call") == 1.0
    assert delta(780, 778, 0.0, 0.04, 0.13, "put") == 0.0
    assert delta(770, 778, 0.0, 0.04, 0.13, "put") == -1.0
    for g in (gamma, vega):
        assert g(780, 778, 0.0, 0.04, 0.13) == 0.0
    assert theta(780, 778, 0.0, 0.04, 0.13, "call") == 0.0


def test_gamma_explodes_near_expiry():
    # ATM gamma with 5 minutes left should dwarf gamma at the open: the whole point of the app
    g_open = gamma(778, 778, 390 / MIN_PER_YEAR, 0.04, 0.13)
    g_late = gamma(778, 778, 5 / MIN_PER_YEAR, 0.04, 0.13)
    assert g_late > 8 * g_open
