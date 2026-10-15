"""Load the live chain (Alpaca, falling back to yfinance), clean it, and get the expected move."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from math import exp, isnan, pi, sqrt
from statistics import median

import pandas as pd

from core.expiry import ET, first_live_expiry_date, years_to_expiry
from core.implied_vol import implied_vol
from data import alpaca_client, yf_fallback
from data.rates import risk_free_rate

# A quote is "too wide" if bid-ask spread > max(MAX_SPREAD_ABS, MAX_SPREAD_PCT * mid).
# The $ floor stops us throwing out cheap options quoted 0.01 x 0.03 (100% wide, but only 2 cents).
MAX_SPREAD_ABS = 0.10
MAX_SPREAD_PCT = 0.25
# Warn when the quoted SPY price and the price implied by the option quotes disagree by more than this
SPOT_MISMATCH_WARN = 0.25
# ...unless the quoted price is a last trade older than this (then a gap is expected, not news)
STALE_TRADE = timedelta(minutes=5)


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
        spot, trade_time = alpaca_client.get_spot(symbol)
        raw = alpaca_client.get_chain(expiry_from, spot, width, symbol)
        source = "alpaca"
    except Exception as e:
        notes.append(f"Alpaca unavailable ({type(e).__name__}: {e}); using yfinance (~15 min delayed).")
        spot, trade_time = yf_fallback.get_spot(symbol)
        raw = yf_fallback.get_chain(expiry_from, spot, width, symbol)
        source = "yfinance"

    chain = clean_chain(raw)
    dropped = len(raw) - len(chain)
    if dropped:
        notes.append(f"Dropped {dropped} of {len(raw)} quotes (zero bid or spread too wide).")

    expiry = raw["expiry"].iloc[0]
    T, r = years_to_expiry(now, expiry), risk_free_rate()
    implied = parity_spot(chain, spot, T, r)
    stale_trade = trade_time is not None and now - trade_time > STALE_TRADE
    if abs(implied - spot) > SPOT_MISMATCH_WARN and not stale_trade:
        notes.append(f"Option prices imply SPY at {implied:.2f}, but its last trade was {spot:.2f} "
                     f"(one of the two feeds is lagging). Using {implied:.2f} so the numbers stay consistent.")
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


def atm_iv(chain: pd.DataFrame, spot: float, T: float, r: float) -> float:
    """Average of the ATM call and put implied vols."""
    k = atm_strike(chain, spot)
    ivs = [implied_vol(option_row(chain, k, cp)["mid"], spot, k, T, r, cp) for cp in ("call", "put")]
    ivs = [v for v in ivs if not isnan(v)]
    if not ivs:
        raise ValueError("can't solve any IV at the ATM strike")
    return sum(ivs) / len(ivs)


def expected_move(chain: pd.DataFrame, spot: float, T: float, r: float) -> dict:
    """Market's expected move to expiry, from the ATM straddle.

    The raw straddle (call mid + put mid) ≈ the AVERAGE size of the move, but only when the
    strike sits exactly at spot. If SPY is between strikes, one leg is partly in the money and
    the straddle includes that intrinsic value, which isn't "expected movement" (it matters most
    near the close). So we take the straddle's implied vol and re-centre it on spot:
        move ≈ sqrt(2/pi) * S * sigma * sqrt(T)       (≈ 0.8 * S * sigma * sqrt(T))
    A 1-standard-deviation move is S * sigma * sqrt(T), about 1.25x bigger.
    """
    k = atm_strike(chain, spot)
    call_mid = option_row(chain, k, "call")["mid"]
    put_mid = option_row(chain, k, "put")["mid"]
    iv = atm_iv(chain, spot, T, r)
    one_sd = spot * iv * sqrt(T)
    move = sqrt(2 / pi) * one_sd
    return {
        "atm_strike": k,
        "call_mid": call_mid,
        "put_mid": put_mid,
        "straddle": call_mid + put_mid,
        "atm_iv": iv,
        "move": move,
        "move_pct": move / spot,
        "low": spot - move,
        "high": spot + move,
        "one_sd_move": one_sd,
    }
