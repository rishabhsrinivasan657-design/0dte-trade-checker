"""Phase 4 live check: run the trade checker on one option from the live chain.

Run:  .venv/bin/python scripts/check_trade.py call 772 [premium]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from checker.trade_check import check_trade
from data.chain import load_market

if __name__ == "__main__":
    load_dotenv()
    option_type, strike = sys.argv[1], float(sys.argv[2])
    premium = float(sys.argv[3]) if len(sys.argv) > 3 else None

    m = load_market()
    c = check_trade(m, option_type, strike, premium)
    em = c.expected
    direction = "rise" if c.move_needed > 0 else "fall"

    print(f"SPY {c.spot:.2f} | {c.minutes_left:.0f} min to close | source={m.source}")
    print(f"{option_type.upper()} {strike:.0f}: bid {c.bid:.2f} / ask {c.ask:.2f} (mid {c.mid:.2f}) "
          f"| you pay ${c.premium:.2f}\n")
    print(f"1. Breakeven at 4 PM:     ${c.breakeven:.2f}  (SPY must {direction} ${abs(c.move_needed):.2f})")
    print(f"2. Expected move today:   ±${em['move']:.2f}  (range {em['low']:.2f} – {em['high']:.2f})")
    print(f"                          → you need {c.move_vs_expected:.1f}× the expected move")
    print(f"3. Chance finishes ITM:   {c.prob_itm:.0%}    chance of profit: {c.prob_profit:.0%}   (IV {c.iv:.1%})")
    print(f"4. Time decay:            ${c.theta_per_hour:.3f}/hour right now")
    print(f"                          if SPY doesn't move for {c.hours_ahead * 60:.0f} min: "
          f"${c.model_value:.2f} → ${c.value_later:.2f}  (−{c.loss_if_flat / c.model_value:.0%})")
    print(f"   Greeks: delta {c.delta:.2f}, gamma {c.gamma:.3f}")
    for n in m.notes + c.notes:
        print("note:", n)
