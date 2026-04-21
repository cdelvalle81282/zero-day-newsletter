"""
Fetch SPX pre-market price via Polygon.io.

Uses SPY pre-market snapshot × 10 as the SPX proxy — SPY trades in extended
hours and its pre-market price × 10 is the standard SPX approximation.
Falls back to the previous SPX close if pre-market data isn't available yet.

Saves to: market_data/premarket_YYYY-MM-DD.json
"""

import json
import os
import sys
from datetime import date, datetime

import requests

import config

BASE_URL = "https://api.polygon.io"


def _get(path, params=None):
    p = dict(params or {})
    p["apiKey"] = config.POLYGON_API_KEY
    resp = requests.get(f"{BASE_URL}{path}", params=p, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _spy_premarket():
    """SPY pre-market last price × 10. Returns (price, label) or (None, None)."""
    data = _get("/v2/snapshot/locale/us/markets/stocks/tickers/SPY")
    snap = data.get("ticker", {})
    pre  = snap.get("preMarket", {})
    price = pre.get("c") or pre.get("close") or snap.get("lastTrade", {}).get("p") or snap.get("day", {}).get("c")
    if price:
        return round(float(price) * 10, 2), "SPY pre-market ×10"
    return None, None


def _spx_snapshot():
    """SPX index snapshot prev close fallback. Returns (price, label) or (None, None)."""
    data = _get("/v3/snapshot", {"ticker.any_of": "I:SPX"})
    for item in data.get("results", []):
        if item.get("error"):
            continue
        val = item.get("session", {}).get("close") or item.get("value")
        if val:
            return round(float(val), 2), "SPX prev close"
    return None, None


def fetch_spx_premarket():
    for fetch in (_spy_premarket, _spx_snapshot):
        try:
            price, label = fetch()
            if price:
                return price, label
        except Exception as e:
            print(f"  WARNING: {fetch.__name__} failed — {e}")
    return None, None


def main():
    if not config.POLYGON_API_KEY:
        print("ERROR: POLYGON_API_KEY not set.")
        sys.exit(1)

    today = str(date.today())
    print(f"Fetching SPX pre-market price for {today}...")

    price, source = fetch_spx_premarket()

    if price is None:
        print("ERROR: Could not get pre-market price from any source.")
        sys.exit(1)

    print(f"  Pre-market SPX: {price:,.2f}  (source: {source})")

    out = {
        "date":          today,
        "fetched_at":    datetime.utcnow().isoformat() + "Z",
        "spx_premarket": price,
        "source":        source,
    }

    os.makedirs(config.MARKET_DATA_DIR, exist_ok=True)
    path = os.path.join(config.MARKET_DATA_DIR, f"premarket_{today}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Saved: {path}")


if __name__ == "__main__":
    main()
