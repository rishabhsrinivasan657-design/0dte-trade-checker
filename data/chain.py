"""Load the live chain (Alpaca, falling back to yfinance), clean it, and get the expected move."""
from dataclasses import dataclass, field
from datetime import date, datetime
from math import exp, pi, sqrt
from statistics import median

import pandas as pd

from core.expiry import ET, first_live_expiry_date, years_to_expiry
from data import alpaca_client, yf_fallback
from data.rates import risk_free_rate

# A quote is "too wide" if bid-ask spread > max(MAX_SPREAD_ABS, MAX_SPREAD_PCT * mid).
# The $ floor stops us throwing out cheap options quoted 0.01 x 0.03 (100% wide, but only 2 cents).
MAX_SPREAD_ABS = 0.10
MAX_SPREAD_PCT = 0.25
# Warn when the quoted SPY price and the price implied by the option quotes disagree by more than this
SPOT_MISMATCH_WARN = 0.25


@dataclass
class MarketSnapshot:
    spot: float                  # SPY price implied by the option quotes (put-call parity): use this
    quoted_spot: float           # last SPY price from the data source
    expiry: date
    T: float                     # years to expiry (core/expiry.py convention)
    r: float                     # risk-free rate
    chain: pd.DataFrame          # cleaned chain, with mid and spread columns
    source: str                  # "alpaca" or "yfinance"
    as_of: datetime
    notes: list[str] = field(default_factory=list)


def load_market(now: datetime | None = None, width: float = 15.0, symbol: str = "SPY") -> MarketSnapshot:
    """Try Alpaca first; if anything fails (no keys, outage, empty chain) use yfinance."""
    now = now or datetime.now(ET)
    expiry_from = first_live_expiry_date(now)
    notes = []
    try:
        spot = alpaca_client.get_spot(symbol)
        raw = alpaca_client.get_chain(expiry_from, spot, width, symbol)
        source = "alpaca"
    except Exception as e:
        notes.append(f"Alpaca unavailable ({type(e).__name__}: {e}); using yfinance (~15 min delayed).")
        spot = yf_fallback.get_spot(symbol)
        raw = yf_fallback.get_chain(expiry_from, spot, width, symbol)
        source = "yfinance"

    chain = clean_chain(raw)
    dropped = len(raw) - len(chain)
    if dropped:
        notes.append(f"Dropped {dropped} of {len(raw)} quotes (zero bid or spread too wide).")

    expiry = raw["expiry"].iloc[0]
    T, r = years_to_expiry(now, expiry), risk_free_rate()
    implied = parity_spot(chain, spot, T, r)
    if abs(implied - spot) > SPOT_MISMATCH_WARN:
        notes.append(f"Option quotes imply SPY {implied:.2f} but last price is {spot:.2f} "
                     f"(quotes are stale vs the stock); using {implied:.2f} so they stay consistent.")
    return MarketSnapshot(implied, spot, expiry, T, r, chain, source, now, notes)


def parity_spot(chain: pd.DataFrame, quoted_spot: float, T: float, r: float, n_strikes: int = 3) -> float:
    """SPY price the option quotes themselves imply, via put-call parity:  S = C - P + K e^(-rT).

    Uses the median over the n strikes nearest the quoted price (median = one bad quote can't skew it).
    Caveat: parity is exact for European options with no dividend before expiry; for 0DTE SPY
    (American-style) the gap is negligible except on an ex-dividend day.
    """
    calls = chain[chain["type"] == "call"].set_index("strike")["mid"]
    puts = chain[chain["type"] == "put"].set_index("strike")["mid"]
    both = sorted(set(calls.index) & set(puts.index), key=lambda k: abs(k - quoted_spot))[:n_strikes]
    if not both:
        return quoted_spot
    return median(calls[k] - puts[k] + k * exp(-r * T) for k in both)


def clean_chain(raw: pd.DataFrame,
                max_spread_abs: float = MAX_SPREAD_ABS,
                max_spread_pct: float = MAX_SPREAD_PCT) -> pd.DataFrame:
    """Add mid/spread, then keep only quotes we'd trust as a price."""
    df = raw.copy()
    df["mid"] = (df["bid"] + df["ask"]) / 2
    df["spread"] = df["ask"] - df["bid"]
    ok = (
        (df["bid"] > 0)                 # zero bid = nobody wants to buy: not a real price
        & (df["ask"] >= df["bid"])      # crossed quote = bad data
        & (df["spread"] <= (max_spread_pct * df["mid"]).clip(lower=max_spread_abs))
    )
    return df[ok].sort_values(["type", "strike"]).reset_index(drop=True)


def atm_strike(chain: pd.DataFrame, spot: float) -> float:
    """Strike closest to spot that has BOTH a clean call and a clean put."""
    calls = set(chain.loc[chain["type"] == "call", "strike"])
    puts = set(chain.loc[chain["type"] == "put", "strike"])
    both = calls & puts
    if not both:
        raise ValueError("no strike has both a clean call and a clean put")
    return min(both, key=lambda k: (abs(k - spot), k))


def option_row(chain: pd.DataFrame, strike: float, option_type: str) -> pd.Series:
    match = chain[(chain["strike"] == strike) & (chain["type"] == option_type)]
    if match.empty:
        raise KeyError(f"no clean {option_type} at strike {strike}")
    return match.iloc[0]


def expected_move(chain: pd.DataFrame, spot: float) -> dict:
    """Market's expected move to expiry, from the ATM straddle (call mid + put mid).

    The straddle ≈ the AVERAGE size of the move. A 1-standard-deviation move is
    about sqrt(pi/2) ≈ 1.25x bigger (that's the normal-distribution relationship).
    """
    k = atm_strike(chain, spot)
    call_mid = option_row(chain, k, "call")["mid"]
    put_mid = option_row(chain, k, "put")["mid"]
    move = call_mid + put_mid
    return {
        "atm_strike": k,
        "call_mid": call_mid,
        "put_mid": put_mid,
        "straddle": move,
        "move": move,
        "move_pct": move / spot,
        "low": spot - move,
        "high": spot + move,
        "one_sd_move": move * sqrt(pi / 2),
    }
