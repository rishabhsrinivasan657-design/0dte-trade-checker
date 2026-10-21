"""Scheduled macro events that move SPY on the day: FOMC decisions and CPI releases.

Sources (fetched 2026-10-20):
  FOMC: federalreserve.gov/monetarypolicy/fomccalendars.htm. The decision is announced on the last day of
        each meeting (2:00 PM ET). The 2025-08-22 "notation vote" was not a meeting and is excluded.
  CPI:  bls.gov/bls/news-release/cpi.htm (archive) + bls.gov/schedule/news_release/cpi.htm (upcoming),
        released 8:30 AM ET. The October 2025 CPI was never published (2025 appropriations lapse).
"""
from datetime import date

FOMC_DECISIONS = {date.fromisoformat(d) for d in [
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]}

CPI_RELEASES = {date.fromisoformat(d) for d in [
    "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10", "2024-05-15", "2024-06-12", "2024-07-11", "2024-08-14",
    "2024-09-11", "2024-10-10", "2024-11-13", "2024-12-11",
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10", "2025-05-13", "2025-06-11", "2025-07-15", "2025-08-12",
    "2025-09-11", "2025-10-24", "2025-12-18",
    "2026-01-13", "2026-02-13", "2026-03-11", "2026-04-10", "2026-05-12", "2026-06-10", "2026-07-14", "2026-08-12",
    "2026-09-11", "2026-10-14", "2026-11-10", "2026-12-10",
]}


def event_type(day: date) -> str | None:
    """'FOMC', 'CPI', 'FOMC+CPI' or None."""
    tags = [name for name, days in (("FOMC", FOMC_DECISIONS), ("CPI", CPI_RELEASES)) if day in days]
    return "+".join(tags) or None
