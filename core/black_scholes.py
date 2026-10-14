"""Black-Scholes pricing for European options. Pure math: no data code.

Units used everywhere in core/:
    S, K   : dollars
    T      : years (we use calendar minutes / 525_600)
    r      : annual risk-free rate, continuous compounding (0.04 = 4%)
    sigma  : annual volatility (0.13 = 13%)
    option_type : "call" or "put"
"""
from math import exp, log, sqrt

from scipy.stats import norm


def d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    d1 = (log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma* (T**0.5))
    d2 = d1 - (sigma* (T**0.5))
    return d1, d2


def bs_price(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
    """Black-Scholes price of a European call or put.

    Hints:
      - call = S*N(d1) - K*e^(-rT)*N(d2)
      - put  = K*e^(-rT)*N(-d2) - S*N(-d1)
      - Use norm.cdf for N(.)
      - Edge case: if T <= 0 or sigma <= 0, return intrinsic value
        (max(S-K, 0) for a call, max(K-S, 0) for a put). 0DTE hits T -> 0 at the close!
      - Raise ValueError for an option_type that isn't "call"/"put".
    """
    if option_type not in ("call", "put"):
        raise ValueError("option_type must be 'call' or 'put'")

    # No time left -> worth exactly its intrinsic value
    if T <= 0 or sigma <= 0:
        if option_type == "call":
            return max(S - K, 0.0)
        else:
            return max(K-S, 0.0)

    d1, d2 = d1_d2(S, K, T, r, sigma)
    discounted_K = K * exp(-r * T)

    if option_type == "call":
        return S * norm.cdf(d1) - discounted_K * norm.cdf(d2)
    else:
        return discounted_K * norm.cdf(-d2) - S * norm.cdf(-d1)


def prob_itm(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
    """Risk-neutral probability the option finishes in the money.

    Hints:
      - call: N(d2)      put: N(-d2)   (so call + put = 1)
      - Edge case T <= 0 or sigma <= 0: it's already decided -> 1.0 if ITM now, else 0.0
    """
    if option_type not in ("call", "put"):
        raise ValueError("option_type must be 'call' or 'put'")

    # No time left -> already decided: 1.0 if in the money, else 0.0
    if T <= 0 or sigma <= 0:
        if option_type == "call":
            return 1.0 if S > K else 0.0
        else:
            return 1.0 if S < K else 0.0

    d1, d2 = d1_d2(S, K, T, r, sigma)

    if option_type == "call":
        return norm.cdf(d2)
    else:
        return norm.cdf(-d2)
