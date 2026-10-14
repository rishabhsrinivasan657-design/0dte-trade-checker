"""Parse OCC option symbols, the format both Alpaca and yfinance use.

SPY261014C00780000 -> root SPY, expiry 2026-10-14, call, strike 780.000
"""
import re
from datetime import datetime

OCC_RE = re.compile(r"^(?P<root>[A-Z]+)(?P<yymmdd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")


def parse_occ(symbol: str) -> dict:
    m = OCC_RE.match(symbol)
    if m is None:
        raise ValueError(f"not an OCC option symbol: {symbol!r}")
    return {
        "expiry": datetime.strptime(m["yymmdd"], "%y%m%d").date(),
        "type": "call" if m["cp"] == "C" else "put",
        "strike": int(m["strike"]) / 1000,
    }

