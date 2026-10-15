"""Phase 3 live check: load the chain, clean it, show the expected move and IVs near the money.

Run:  .venv/bin/python scripts/show_market.py [--yf]     (--yf forces the yfinance fallback)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from core.expiry import minutes_to_expiry
from core.implied_vol import implied_vol
from data.chain import expected_move, load_market

if __name__ == "__main__":
    load_dotenv()
    if "--yf" in sys.argv:
        import data.alpaca_client as ac
        ac.get_spot = lambda *a, **k: (_ for _ in ()).throw(ac.AlpacaUnavailable("forced by --yf"))

    m = load_market()
    T, r = m.T, m.r
    print(f"{m.as_of:%Y-%m-%d %H:%M} ET | source={m.source} | SPY {m.spot:.2f} "
          f"(implied by options; last trade {m.quoted_spot:.2f}) | "
          f"expiry {m.expiry} | {minutes_to_expiry(m.as_of, m.expiry):.0f} min left | r={r:.2%}")
    for n in m.notes:
        print("note:", n)

    em = expected_move(m.chain, m.spot, T, r)
    print(f"\nATM {em['atm_strike']:.0f} straddle = {em['call_mid']:.2f} call + {em['put_mid']:.2f} put "
          f"= ${em['straddle']:.2f}  | ATM IV {em['atm_iv']:.1%} → expected move ±${em['move']:.2f} ({em['move_pct']:.2%})")
    print(f"Expected range by close: {em['low']:.2f} – {em['high']:.2f}   (1-sd move ≈ ${em['one_sd_move']:.2f})")

    print(f"\n{'strike':>7} {'type':>5} {'bid':>6} {'ask':>6} {'mid':>6} {'our IV':>7}")
    near = m.chain[(m.chain["strike"] - em["atm_strike"]).abs() <= 3]
    for _, row in near.iterrows():
        iv = implied_vol(row["mid"], m.spot, row["strike"], T, r, row["type"])
        print(f"{row['strike']:7.0f} {row['type']:>5} {row['bid']:6.2f} {row['ask']:6.2f} {row['mid']:6.2f} {iv:7.2%}")
