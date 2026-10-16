"""Live sanity sweep: check every strike near SPY against rules the numbers must always obey.

Run while the app is up:  .venv/bin/python scripts/live_sweep.py [http://localhost:8000]
Prices move every few seconds, so this checks RELATIONSHIPS (which must always hold),
not exact values. Exit code 1 if any hard rule fails.
"""
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
WINDOW = 8.0


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=60)
    m = client.get("/api/market").json()
    spot, open_ = m["spot"], m["market_open"]
    print(f"SPY {spot:.2f} | {m['source']} | expiry {m['expiry']} | {m['minutes_left']:.0f} min left | market open: {open_}")
    fails, warns, results, times = [], [], {}, []

    for cp in ("call", "put"):
        strikes = [q["strike"] for q in m["quotes"][cp] if abs(q["strike"] - spot) <= WINDOW]
        for k in strikes:
            t0 = time.perf_counter()
            r = client.get("/api/check", params={"type": cp, "strike": k})
            times.append(time.perf_counter() - t0)
            name = f"{cp} {k:g}"
            if r.status_code != 200:
                fails.append(f"{name}: HTTP {r.status_code} {r.text[:120]}")
                continue
            j = r.json()
            t, ev, sc = j["trade"], j["evidence"], j["scenarios"]
            results[(cp, k)] = t
            be = k + t["premium"] if cp == "call" else k - t["premium"]
            check = lambda ok, msg: None if ok else fails.append(f"{name}: {msg}")
            check(abs(t["breakeven"] - be) < 1e-6, f"breakeven {t['breakeven']} != {be}")
            check(abs(t["premium"] - t["ask"]) < 1e-9, "premium should default to the ask")
            check(0 <= t["prob_profit"] <= t["prob_itm"] + 1e-9 <= 1 + 1e-9, f"probabilities out of order {t['prob_profit']:.3f} / {t['prob_itm']:.3f}")
            check(0.02 < t["iv"] < 3, f"implied vol looks wrong: {t['iv']:.3f}")
            check(t["bid"] <= t["mid"] <= t["ask"], "bid/mid/ask out of order")
            check(t["expected"]["low"] < spot < t["expected"]["high"], "expected range doesn't contain spot")
            check(sc["prices"] == sorted(sc["prices"], reverse=True), "what-if rows not sorted high -> low")
            last = [row[-1] for row in sc["pnl"]]
            intrinsic = [max(p - k, 0) if cp == "call" else max(k - p, 0) for p in sc["prices"]]
            check(all(abs(a - (b - t["premium"])) < 1e-6 for a, b in zip(last, intrinsic)), "what-if 4:00 column != intrinsic - premium")
            if ev:
                check(0 <= ev["realistic_profit"] <= 1 and 0 <= ev["realistic_itm"] <= 1, "realistic chance outside 0..1")
                if open_ and t["minutes_left"] <= 390 and ev["similar"] is None:
                    warns.append(f"{name}: no similar-trades match")
            d = j["decision"]
            check(1 <= len(d["reasons"]) <= 3, "decision should give 1-3 reasons")
            if open_ and t["minutes_left"] <= 390:
                check(d["available"] and d["rating"] in {"avoid", "poor", "typical"}, "no rating during the session")
                results.setdefault("_ratings", {})[(cp, k)] = d.get("score")
            if t["theta_per_hour"] > 0:
                warns.append(f"{name}: positive theta {t['theta_per_hour']:.4f}/hr (possible for deep ITM puts)")
            if open_ and j["model"] is None:
                warns.append(f"{name}: ML model block missing during market hours")

    # scan must agree with check for the same strike
    scan = {cp: {r["strike"]: r for r in client.get("/api/scan", params={"type": cp}).json()["rows"]} for cp in ("call", "put")}
    for (cp, k), score in results.pop("_ratings", {}).items():
        if k in scan[cp] and score is not None and abs(scan[cp][k]["score"] - score) > 0.15:
            warns.append(f"{cp} {k:g}: scan score {scan[cp][k]['score']:.2f} vs check {score:.2f} (prices moved between calls?)")

    # monotonicity across strikes: higher call strike -> lower odds; higher put strike -> higher odds
    for cp, sign in (("call", -1), ("put", 1)):
        ks = sorted(k for c, k in results if c == cp)
        probs = [results[(cp, k)]["prob_itm"] for k in ks]
        bad = [(a, b) for a, b, pa, pb in zip(ks, ks[1:], probs, probs[1:]) if sign * (pb - pa) < -0.02]
        if bad:
            fails.append(f"{cp}: odds not monotonic across strikes at {bad[:3]}")

    print(f"Checked {len(results)} options in {sum(times):.1f}s (slowest {max(times):.2f}s, median {sorted(times)[len(times)//2]:.2f}s)")
    for w in warns:
        print("  warn:", w)
    for f in fails:
        print("  FAIL:", f)
    print("RESULT:", "PASS" if not fails else f"{len(fails)} FAILURES", f"({len(warns)} warnings)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
