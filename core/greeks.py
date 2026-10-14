"""Black-Scholes Greeks. Same units as core/black_scholes.py.

Reuse d1_d2 from black_scholes. You'll need:
    norm.cdf(x)  -> N(x),  the standard normal CDF
    norm.pdf(x)  -> n(x),  the standard normal density  e^(-x^2/2) / sqrt(2*pi)

Edge case for all of them: if T <= 0 or sigma <= 0, return the "expired" value:
    delta -> 1.0 / 0.0 (call ITM / OTM), -1.0 / 0.0 for a put
    gamma, vega, theta -> 0.0
"""
from math import exp, sqrt

from scipy.stats import norm

from core.black_scholes import d1_d2


def _expired(T: float, sigma: float) -> bool:
    return T <= 0 or sigma <= 0


def _check_type(option_type: str) -> None:
    if option_type not in ("call", "put"):
        raise ValueError("option_type must be 'call' or 'put'")


def delta(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
    """dPrice/dS.  Hint: call = N(d1), put = N(d1) - 1."""
    _check_type(option_type)
    if _expired(T, sigma):
        if option_type == "call":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0

    d1, _ = d1_d2(S, K, T, r, sigma)
    if option_type == "call":
        return norm.cdf(d1)
    return norm.cdf(d1) - 1.0


def gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """d²Price/dS², the same for calls and puts.  Hint: n(d1) / (S * sigma * sqrt(T))."""
    if _expired(T, sigma):
        return 0.0

    d1, _ = d1_d2(S, K, T, r, sigma)
    return norm.pdf(d1) / (S * sigma * sqrt(T))


def vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """dPrice/dsigma per 1.00 of vol (NOT per 1%), the same for calls and puts.

    Hint: S * n(d1) * sqrt(T).   (The IV solver in Phase 2 divides by this!)
    """
    if _expired(T, sigma):
        return 0.0

    d1, _ = d1_d2(S, K, T, r, sigma)
    return S * norm.pdf(d1) * sqrt(T)


def theta(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
    """dPrice/dt per YEAR as calendar time passes (negative for a long option, usually).

    Hints:
      common term = -S * n(d1) * sigma / (2 * sqrt(T))
      call: common - r * K * e^(-rT) * N(d2)
      put : common + r * K * e^(-rT) * N(-d2)
    We'll convert to per-hour later: per-year / (365 * 24).
    """
    _check_type(option_type)
    if _expired(T, sigma):
        return 0.0

    d1, d2 = d1_d2(S, K, T, r, sigma)
    decay = -S * norm.pdf(d1) * sigma / (2 * sqrt(T))   # the volatility part: the big one at 0DTE
    discounted_K = K * exp(-r * T)
    if option_type == "call":
        return decay - r * discounted_K * norm.cdf(d2)
    return decay + r * discounted_K * norm.cdf(-d2)
