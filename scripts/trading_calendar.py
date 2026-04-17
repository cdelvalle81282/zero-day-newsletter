"""
Trading calendar utilities.
Uses the 'holidays' package for NYSE-observed holidays.
"""

from datetime import date, timedelta
import holidays


def is_trading_day(d=None):
    """Return True if d is a NYSE trading day (not weekend, not holiday)."""
    d = d or date.today()
    if d.weekday() >= 5:        # Saturday=5, Sunday=6
        return False
    nyse = holidays.NYSE(years=d.year)
    return d not in nyse


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

    The newsletter describes the PREVIOUS trading day's action:
      - Monday newsletter → uses Friday's data
      - Tuesday newsletter → uses Monday's data
      - A day after a holiday → uses the last trading day before it
    """
    d = newsletter_date or date.today()
    return previous_trading_day(d)
