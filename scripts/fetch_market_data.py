"""
Zero Day Newsletter — Market Data Fetcher (Polygon.io)

Two-phase fetch:

  Phase 1 — options (run at 3:50 PM ET, while 0DTE contracts still active):
      python3 scripts/fetch_market_data.py --mode options

  Phase 2 — quotes (run at 4:35 PM ET, after market close):
      python3 scripts/fetch_market_data.py --mode quotes

  Full fetch (both phases at once, useful for testing):
      python3 scripts/fetch_market_data.py

  Backfill a past date:
      python3 scripts/fetch_market_data.py --mode quotes --date 2026-04-16 --force
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from urllib.parse import urlparse, parse_qs

import requests

import config
from trading_calendar import is_trading_day

BASE_URL = "https://api.polygon.io"

def _empty_options():
    return {
        "today_volume": None, "volume_20day_avg": None, "vs_average_pct": None,
        "call_volume": None, "put_volume": None, "put_call_ratio": None,
        "top_call_strikes": [], "top_put_strikes": [], "the_number": None,
    }


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _get(path, params=None, timeout=30):
    p = dict(params or {})
    p["apiKey"] = config.POLYGON_API_KEY
    resp = requests.get(f"{BASE_URL}{path}", params=p, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _get_or_none(path, params=None, timeout=30):
    """Like _get but returns None on 403 (plan restriction) instead of raising."""
    try:
        return _get(path, params, timeout)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            return None
        raise


# ── Quotes ────────────────────────────────────────────────────────────────────

def fetch_quotes(target_date=None):
    """
    Fetch closing quotes for SPX, VIX, SPY, QQQ.
    Live (today): snapshot for SPX/VIX, aggs for SPY/QQQ.
    Past date: aggs for all four (snapshot only has current session data).
    """
    target_date = target_date or date.today()
    results     = {}

    results.update(_fetch_stock_aggs(["SPY", "QQQ"], target_date))

    if target_date < date.today():
        results.update(_fetch_index_aggs(target_date))
    else:
        results.update(_fetch_index_snapshots())

    # Compute display colors
    for key, d in results.items():
        pct = d.get("pct_change", 0) or 0
        if key == "vix":
            d["color"]         = "#22C55E" if pct >= 0 else "#CC3333"
            d["display_color"] = "#22C55E" if pct <= 0 else "#CC3333"
        else:
            d["color"]         = "#22C55E" if pct >= 0 else "#CC3333"
            d["display_color"] = "#22C55E" if pct >= 0 else "#CC3333"

    return results


def _fetch_stock_aggs(tickers, target_date):
    """Fetch OHLC for tickers on target_date via daily aggs range endpoint."""
    out       = {}
    date_str  = str(target_date)
    prev_str  = str(target_date - timedelta(days=7))
    for ticker in tickers:
        try:
            data = _get(f"/v2/aggs/ticker/{ticker}/range/1/day/{prev_str}/{date_str}",
                        {"adjusted": "true", "sort": "asc", "limit": 10})
            bars = data.get("results", [])
            if not bars:
                continue
            bar     = bars[-1]
            prev_b  = bars[-2] if len(bars) >= 2 else None
            close   = bar.get("c")
            prev_c  = prev_b.get("c") if prev_b else bar.get("o")
            pct     = round((close - prev_c) / prev_c * 100, 2) if close and prev_c else 0
            net     = round(close - prev_c, 2) if close and prev_c else 0
            key     = ticker.lower()
            out[key] = {"close": round(close, 2), "pct_change": pct, "net_change": net}
        except Exception as e:
            print(f"  WARNING: Could not fetch {ticker} aggs — {e}")
    return out


def _fetch_index_snapshots():
    """
    Fetch SPX and VIX from Polygon v3 snapshot.
    Returns empty dict if plan doesn't include index data (NOT_ENTITLED).
    """
    data = _get_or_none("/v3/snapshot", {"ticker.any_of": "I:SPX,I:VIX"})
    if data is None:
        print("  WARNING: Index data (SPX/VIX) not available — upgrade to Polygon Indices plan.")
        return {}
    out = {}
    for item in data.get("results", []):
        if item.get("error") == "NOT_ENTITLED":
            print(f"  WARNING: {item.get('ticker')} not entitled on current Polygon plan.")
            continue
        ticker  = item.get("ticker", "")
        session = item.get("session", {})
        close   = session.get("close") or item.get("value")
        prev    = session.get("previous_close")
        change  = session.get("change", 0) or 0
        pct     = session.get("change_percent", 0) or 0
        if close and prev and pct == 0:
            pct = round((float(close) - float(prev)) / float(prev) * 100, 2)
        key = {"I:SPX": "spx", "I:VIX": "vix"}.get(ticker)
        if not key or not close:
            continue
        out[key] = {
            "close":      round(float(close), 2),
            "pct_change": round(float(pct), 2),
            "net_change": round(float(change), 2),
        }
    return out



def _fetch_index_aggs(target_date):
    """Fetch SPX and VIX closing prices for a past date via daily aggs."""
    out      = {}
    date_str = str(target_date)
    prev_str = str(target_date - timedelta(days=7))
    for ticker, key in [("I:SPX", "spx"), ("I:VIX", "vix")]:
        try:
            data = _get(f"/v2/aggs/ticker/{ticker}/range/1/day/{prev_str}/{date_str}",
                        {"adjusted": "true", "sort": "asc", "limit": 10})
            bars = data.get("results", [])
            if len(bars) < 2:
                continue
            close  = bars[-1]["c"]
            prev_c = bars[-2]["c"]
            pct    = round((close - prev_c) / prev_c * 100, 2)
            out[key] = {"close": round(close, 2), "pct_change": pct,
                        "net_change": round(close - prev_c, 2)}
        except Exception as e:
            print(f"  WARNING: Could not fetch historical {ticker} — {e}")
    return out


# ── SPX price history (50-day MA) ─────────────────────────────────────────────

def fetch_spx_history(target_date=None):
    """
    Fetch SPX daily history. Returns (ma_50, pct_change).
    """
    target_date = target_date or date.today()
    from_date   = str(target_date - timedelta(days=90))
    to_date     = str(target_date)

    try:
        data = _get(f"/v2/aggs/ticker/I:SPX/range/1/day/{from_date}/{to_date}",
                    {"adjusted": "true", "sort": "asc", "limit": 100})
        bars = data.get("results", [])
    except Exception as e:
        print(f"  WARNING: Could not fetch SPX history — {e}")
        return None, None

    if not bars:
        print("  WARNING: No SPX history bars returned.")
        return None, None

    closes  = [b["c"] for b in bars]
    last_50 = closes[-50:] if len(closes) >= 50 else closes
    ma_50   = round(sum(last_50) / len(last_50), 2)

    pct_change = None
    if len(closes) >= 2:
        prev_c, today_c = closes[-2], closes[-1]
        if prev_c:
            pct_change = round((today_c - prev_c) / prev_c * 100, 2)

    return ma_50, pct_change


# ── 0DTE options chain ────────────────────────────────────────────────────────

def fetch_0dte_chain(target_date=None):
    """
    Fetch all SPX 0DTE option contract snapshots from Polygon.
    Paginates until all contracts for today are retrieved.
    Returns a flat list of contract dicts.
    """
    target_date = target_date or date.today()
    exp_date    = str(target_date)
    contracts   = []
    cursor      = None

    print(f"  Fetching SPX 0DTE options chain ({exp_date})...")
    page_count = 0
    while page_count < 50:
        params = {"expiration_date": exp_date, "limit": 250}
        if cursor:
            params["cursor"] = cursor

        try:
            data = _get("/v3/snapshot/options/SPX", params)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                print("  WARNING: Polygon options endpoint returned 403 — "
                      "your plan may not include options data.")
            raise

        contracts.extend(data.get("results", []))
        page_count += 1

        next_url = data.get("next_url", "")
        if not next_url:
            break
        cursor = parse_qs(urlparse(next_url).query).get("cursor", [None])[0]
        if not cursor:
            break

    print(f"  Retrieved {len(contracts)} contracts.")
    return contracts


def fetch_0dte_volume(contracts):
    """
    Derive total volume, top strikes, and rolling average from contract snapshots.
    Also finds The Number — the biggest intraday % winner.
    """
    today_volume = 0
    call_volumes = {}
    put_volumes  = {}
    the_number   = None
    best_pct     = 0

    for c in contracts:
        details  = c.get("details", {})
        day      = c.get("day", {})
        ctype    = details.get("contract_type", "").lower()   # "call" or "put"
        strike   = float(details.get("strike_price", 0))
        vol      = int(day.get("volume", 0) or 0)
        open_px  = day.get("open",  0) or 0
        high_px  = day.get("high",  0) or 0
        last_px  = day.get("close", 0) or 0

        today_volume += vol
        vol_map = call_volumes if ctype == "call" else put_volumes
        vol_map[strike] = vol_map.get(strike, 0) + vol

        # The Number: biggest intraday % gainer with meaningful open + volume
        if open_px >= 0.05 and high_px > open_px and vol >= 50:
            pct = (high_px - open_px) / open_px * 100
            if pct > best_pct:
                best_pct   = pct
                the_number = {
                    "strike":       strike,
                    "type":         ctype,
                    "open":         open_px,
                    "high":         high_px,
                    "last":         last_px,
                    "volume":       vol,
                    "pct_gain":     round(pct, 0),
                    "gain_dollars": round((high_px - open_px) * 100, 0),
                }

    avg = _get_rolling_average(today_volume)

    top_calls = sorted(call_volumes.items(), key=lambda x: x[1], reverse=True)[:3]
    top_puts  = sorted(put_volumes.items(),  key=lambda x: x[1], reverse=True)[:3]

    total_call_vol = sum(call_volumes.values())
    total_put_vol  = sum(put_volumes.values())
    pc_ratio = round(total_put_vol / total_call_vol, 2) if total_call_vol else None

    return {
        "today_volume":     today_volume,
        "volume_20day_avg": avg,
        "vs_average_pct":   round((today_volume / avg - 1) * 100, 1) if avg else None,
        "call_volume":      total_call_vol,
        "put_volume":       total_put_vol,
        "put_call_ratio":   pc_ratio,
        "top_call_strikes": [{"strike": s, "volume": v} for s, v in top_calls],
        "top_put_strikes":  [{"strike": s, "volume": v} for s, v in top_puts],
        "the_number":       the_number,
    }


def _get_rolling_average(today_volume):
    if not os.path.exists(config.MARKET_DATA_DIR):
        return today_volume
    files = sorted([
        f for f in os.listdir(config.MARKET_DATA_DIR)
        if f.endswith(".json") and not f.startswith("premarket_")
    ])[-20:]
    volumes = []
    for fname in files:
        path = os.path.join(config.MARKET_DATA_DIR, fname)
        with open(path) as f:
            data = json.load(f)
        vol = data.get("options", {}).get("today_volume")
        if vol:
            volumes.append(vol)
    return round(sum(volumes) / len(volumes)) if volumes else today_volume


# ── Save / load ───────────────────────────────────────────────────────────────

def save(data):
    os.makedirs(config.MARKET_DATA_DIR, exist_ok=True)
    path = os.path.join(config.MARKET_DATA_DIR, f"{data['date']}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved: {path}")
    return path


def load_existing(today):
    path = os.path.join(config.MARKET_DATA_DIR, f"{today}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["options", "quotes", "full"], default="full")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    if not config.POLYGON_API_KEY:
        print("ERROR: POLYGON_API_KEY is not set. Add it to .env or config.py.")
        sys.exit(1)

    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            print("ERROR: Invalid --date format. Use YYYY-MM-DD.")
            sys.exit(1)
    else:
        target_date = date.today()

    today = str(target_date)

    if not args.force and not is_trading_day(target_date):
        print(f"{today} is not a trading day. Skipping. Use --force to override.")
        sys.exit(0)

    print(f"Fetching market data [{args.mode}] for {today}"
          f"{'  (backfill)' if target_date < date.today() else ''}...")

    # ── Phase 1: Options chain (3:50 PM ET) ──────────────────────────────────
    if args.mode in ("options", "full"):
        try:
            contracts = fetch_0dte_chain(target_date)
            options   = fetch_0dte_volume(contracts)
            print(f"  Options volume: {options.get('today_volume', 0):,}")
            tn = options.get("the_number")
            if tn:
                print(f"  Best 0DTE trade: SPX {tn['strike']:.0f} {tn['type'].upper()} "
                      f"{tn['open']:.2f} -> {tn['high']:.2f} (+{tn['pct_gain']:.0f}%)")
        except Exception as e:
            print(f"  WARNING: Could not fetch options chain — {e}")
            options = load_existing(today).get("options") or _empty_options()

        existing = load_existing(today)
        payload  = {
            "date":       today,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "spx": existing.get("spx", {}),
            "vix": existing.get("vix", {}),
            "spy": existing.get("spy", {}),
            "qqq": existing.get("qqq", {}),
            "options": options,
        }
        save(payload)

    # ── Phase 2: Quotes + MA (4:35 PM ET) ────────────────────────────────────
    if args.mode in ("quotes", "full"):
        print("  Fetching SPX history (50-day MA)...")
        ma_50, spx_pct = fetch_spx_history(target_date)

        print("  Fetching quotes (SPX, VIX, SPY, QQQ)...")
        quotes = fetch_quotes(target_date)

        # Override SPX pct_change with candle-derived value (more reliable)
        if "spx" in quotes and spx_pct is not None:
            quotes["spx"]["pct_change"] = spx_pct
            close = quotes["spx"].get("close")
            if close:
                quotes["spx"]["net_change"] = round(close * spx_pct / 100, 2)
        if "spx" in quotes:
            quotes["spx"]["ma_50"] = ma_50

        existing = load_existing(today) if args.mode == "quotes" else {}
        payload  = {
            "date":       today,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            **quotes,
            "options": existing.get("options") or _empty_options(),
        }
        path = save(payload)

        print("\n-- Market Snapshot --------------------------")
        for ticker in ["spx", "vix", "spy", "qqq"]:
            d     = payload.get(ticker, {})
            close = d.get("close")
            pct   = d.get("pct_change", 0) or 0
            arrow = "+" if pct >= 0 else "-"
            if close:
                print(f"  {ticker.upper():4s}  {close:>10,.2f}  {arrow} {abs(pct):.2f}%")
            else:
                print(f"  {ticker.upper():4s}  unavailable")
        print(f"\n  SPX 50-day MA:  {f'{ma_50:,.2f}' if ma_50 else 'unavailable'}")
        opts = payload.get("options", {})
        vol  = opts.get("today_volume")
        print(f"  0DTE Volume:    {f'{vol:,}' if vol is not None else 'run --mode options before 4 PM'}")
        print(f"\nDone. Saved to {path}")


if __name__ == "__main__":
    main()
