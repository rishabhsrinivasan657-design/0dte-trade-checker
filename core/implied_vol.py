"""Implied volatility: run Black-Scholes backwards (market price -> sigma).

Method: Newton-Raphson (fast) inside a bisection bracket (safe).
  - Newton jumps by  sigma_new = sigma - (model_price - market_price) / vega.
  - At 0DTE vega can be ~0, so that division explodes. Whenever Newton's jump is
    unusable (tiny vega, or it would land outside the bracket), we fall back to a
    bisection step: try the middle of [lo, hi]. Slower, but it can't fail.
Returns nan when no volatility can produce the price (e.g. price below intrinsic).
"""
from math import exp, isfinite, nan, pi, sqrt

from core.black_scholes import bs_price
from core.greeks import vega

SIGMA_LO, SIGMA_HI = 1e-4, 5.0   # 0.01% .. 500% annual vol


def no_arbitrage_bounds(S: float, K: float, T: float, r: float, option_type: str) -> tuple[float, float]:
    """The cheapest and dearest a European option can sensibly cost.

    call: between  max(S - K e^-rT, 0)  and  S
    put : between  max(K e^-rT - S, 0)  and  K e^-rT
    """
    disc_K = K * exp(-r * T)
    if option_type == "call":
        return max(S - disc_K, 0.0), S
    if option_type == "put":
        return max(disc_K - S, 0.0), disc_K
    raise ValueError("option_type must be 'call' or 'put'")


def implied_vol(
    price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str = "call",
    tol: float = 1e-10,
    max_iter: int = 100,
) -> float:
    """Volatility sigma such that bs_price(S, K, T, r, sigma) == price. nan if none exists."""
    if T <= 0 or not isfinite(price):
        return nan
    lower, upper = no_arbitrage_bounds(S, K, T, r, option_type)
    if price <= lower or price >= upper:
        return nan   # all intrinsic (no time value) or impossibly expensive: no sigma fits

    lo, hi = SIGMA_LO, SIGMA_HI
    if bs_price(S, K, T, r, hi, option_type) < price:
        return nan   # would need > 500% vol: treat as bad data

    # Starting guess: the ATM rule of thumb  price ≈ 0.4 * S * sigma * sqrt(T)  solved for sigma
    sigma = min(max(sqrt(2 * pi / T) * price / S, lo), hi)

    for _ in range(max_iter):
        diff = bs_price(S, K, T, r, sigma, option_type) - price
        if abs(diff) < tol:
            return sigma

        # Price rises with sigma, so 'diff' tells us which side of the answer we're on
        if diff > 0:
            hi = sigma
        else:
            lo = sigma

        v = vega(S, K, T, r, sigma)
        newton = sigma - diff / v if v > 1e-12 else nan
        sigma = newton if lo < newton < hi else (lo + hi) / 2   # Newton if usable, else bisect

        if hi - lo < 1e-12:
            return sigma
    return sigma
