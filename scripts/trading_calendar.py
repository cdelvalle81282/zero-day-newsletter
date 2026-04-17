"""
Trading calendar utilities.
Uses the 'holidays' package for NYSE-observed holidays.
"""

from datetime import date, timedelta
from functools import lru_cache
import holidays


@lru_cache(maxsize=10)
def _nyse_holidays(year):
    """Cached NYSE holiday set per year — avoids rebuilding on every call."""
    return holidays.NYSE(years=year)


def is_trading_day(d=None):
    """Return True if d is a NYSE trading day (not weekend, not holiday)."""
    d = d or date.today()
    if d.weekday() >= 5:
        return False
    return d not in _nyse_holidays(d.year)


def previous_trading_day(d=None):
    """Return the most recent trading day before d (exclusive)."""
    d = d or date.today()
    prev = d - timedelta(days=1)
    while not is_trading_day(prev):
        prev -= timedelta(days=1)
    return prev


def next_trading_day(d=None):
    """Return the next trading day after d (exclusive)."""
    d = d or date.today()
    nxt = d + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def trading_days_in_range(start, end):
    """Return list of trading days between start and end (inclusive)."""
    days = []
    d = start
    while d <= end:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def market_data_date_for_newsletter(newsletter_date=None):
    """
    Return the trading day whose market data belongs in a given newsletter.
    Monday newsletter → Friday's data; post-holiday → last trading day before it.
    """
    d = newsletter_date or date.today()
    return previous_trading_day(d)
