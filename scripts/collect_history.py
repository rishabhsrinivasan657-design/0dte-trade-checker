"""Build / update the historical archive in data/cache/. Safe to re-run: it only fetches what's missing.

Run:  .venv/bin/python scripts/collect_history.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from data import archive

if __name__ == "__main__":
    load_dotenv()
    print("SPY 5-minute bars…", flush=True)
    spy = archive.update_spy()
    print(f"  {len(spy):,} bars, {spy.index[0]:%Y-%m-%d} → {spy.index[-1]:%Y-%m-%d}")
    print("VIX daily…", flush=True)
    vix = archive.update_vix()
    print(f"  {len(vix):,} days")
    print("VIX1D daily…", flush=True)
    print(f"  {len(archive.update_vix1d()):,} days")
    print("0DTE option bars (one file per day)…", flush=True)
    n = archive.update_options()
    print(f"  fetched {n} new days; {len(archive.option_days())} days on disk")
