"""Risk-free rate for Black-Scholes. For 0DTE it barely matters (T is tiny), but we use a real one.

Source: 13-week T-bill yield (^IRX on Yahoo, quoted in percent). Falls back to 4%.
Caveat: ^IRX is a discount yield, not a continuously-compounded rate; at 0DTE the difference is negligible.
"""
import yfinance as yf

FALLBACK_RATE = 0.04


def risk_free_rate() -> float:
    try:
        rate = float(yf.Ticker("^IRX").history(period="5d")["Close"].dropna().iloc[-1]) / 100
        return rate if 0 <= rate < 0.2 else FALLBACK_RATE
    except Exception:
        return FALLBACK_RATE
