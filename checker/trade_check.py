"""Check one 0DTE option before buying: breakeven, expected move, chances, and time decay.

Prices used:
  - premium (what the trader pays) defaults to the ASK: that's what a buyer really pays.
  - the model (IV, probabilities, Greeks) uses the MID: our best estimate of fair value.
"""
from dataclasses import dataclass, field
from math import isnan

from core.black_scholes import bs_price, prob_itm
from core.expiry import MINUTES_PER_YEAR
from core.greeks import delta, gamma, theta
from core.implied_vol import implied_vol
from data.chain import MarketSnapshot, expected_move, option_row

HOURS_PER_YEAR = MINUTES_PER_YEAR / 60   # 8,760: theta is per year, we want per hour
FINAL_MINUTES_WARNING = 15


@dataclass
class TradeCheck:
    option_type: str
    strike: float
    premium: float               # what the trader pays (default: ask)
    bid: float
    ask: float
    mid: float
    spot: float
    minutes_left: float

    breakeven: float             # 1. SPY price at 4 PM where you get your money back
    move_needed: float           #    breakeven - spot (positive = SPY must rise)
    expected: dict               # 2. ATM straddle expected move (from data.chain.expected_move)
    move_vs_expected: float      #    |move_needed| / expected move: 1.0 = "needs one full expected move"

    iv: float                    # this option's implied vol (from its mid)
    prob_itm: float              # 3. chance it finishes in the money (risk-neutral, N(d2))
    prob_profit: float           #    chance SPY finishes past breakeven (same IV)

    theta_per_hour: float        # 4. $ lost per hour right now if SPY doesn't move
    hours_ahead: float           #    1 hour, or less if the close is sooner
    model_value: float           #    Black-Scholes value now at self.iv (≈ mid)
    value_later: float           #    model value after hours_ahead if SPY doesn't move
    delta: float
    gamma: float
    notes: list[str] = field(default_factory=list)

    @property
    def loss_if_flat(self) -> float:
        """$ the option loses over hours_ahead if SPY sits still (model value now - later)."""
        return self.model_value - self.value_later


def check_trade(market: MarketSnapshot, option_type: str, strike: float,
                premium: float | None = None) -> TradeCheck:
    S, T, r, chain = market.spot, market.T, market.r, market.chain
    try:
        row = option_row(chain, strike, option_type)
    except KeyError:
        available = sorted(chain.loc[chain["type"] == option_type, "strike"])
        raise ValueError(f"no clean {option_type} quote at {strike}. "
                         f"Available: {available[0]:.0f}–{available[-1]:.0f}") from None

    notes = []
    premium = float(row["ask"]) if premium is None else float(premium)
    if premium <= 0:
        raise ValueError("premium must be positive")

    # 1. Breakeven
    breakeven = strike + premium if option_type == "call" else strike - premium
    move_needed = breakeven - S

    # 2. Expected move
    em = expected_move(chain, S, T, r)
    move_vs_expected = abs(move_needed) / em["move"]

    # 3. Probabilities, using this option's own IV
    iv = implied_vol(row["mid"], S, strike, T, r, option_type)
    if isnan(iv):
        iv = em["atm_iv"]
        notes.append("This option's price is almost all intrinsic value, so its own IV can't be "
                     f"solved; using the ATM IV ({iv:.1%}) instead.")
    p_itm = prob_itm(S, strike, T, r, iv, option_type)
    p_profit = prob_itm(S, breakeven, T, r, iv, option_type)

    # 4. Time decay
    minutes_left = T * MINUTES_PER_YEAR
    theta_hr = theta(S, strike, T, r, iv, option_type) / HOURS_PER_YEAR
    hours_ahead = min(1.0, minutes_left / 60)
    model_now = bs_price(S, strike, T, r, iv, option_type)
    T_later = 0.0 if hours_ahead < 1.0 else T - hours_ahead / HOURS_PER_YEAR   # close comes first: exactly 0
    later = bs_price(S, strike, T_later, r, iv, option_type)

    if minutes_left < FINAL_MINUTES_WARNING:
        notes.append("Final minutes before the close: prices include risk the 4:00 PM clock doesn't "
                     "count (e.g. after-hours moves before the exercise deadline), so implied vols "
                     "jump and these numbers are least reliable.")
    if premium > row["ask"] + 1e-9:
        notes.append(f"Premium ${premium:.2f} is above the current ask ${row['ask']:.2f}.")

    return TradeCheck(
        option_type=option_type, strike=strike, premium=premium,
        bid=float(row["bid"]), ask=float(row["ask"]), mid=float(row["mid"]),
        spot=S, minutes_left=minutes_left,
        breakeven=breakeven, move_needed=move_needed, expected=em, move_vs_expected=move_vs_expected,
        iv=iv, prob_itm=p_itm, prob_profit=p_profit,
        theta_per_hour=theta_hr, hours_ahead=hours_ahead, model_value=model_now, value_later=later,
        delta=delta(S, strike, T, r, iv, option_type), gamma=gamma(S, strike, T, r, iv),
        notes=notes,
    )

