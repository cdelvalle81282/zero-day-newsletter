"""
Zero Day Newsletter — Market Data Fetcher

Two-phase fetch to work around Schwab's options chain expiry at 4 PM ET:

  Phase 1 — options (run at 3:50 PM ET, while 0DTE contracts still active):
      python3 scripts/fetch_market_data.py --mode options

  Phase 2 — quotes (run at 4:35 PM ET, after market close):
      python3 scripts/fetch_market_data.py --mode quotes

  Full fetch (both phases at once, useful for testing):
      python3 scripts/fetch_market_data.py
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta

from schwab import auth

import config
from trading_calendar import is_trading_day

# ── Setup ─────────────────────────────────────────────────────────────────────

def get_client():
    """Load existing token or abort with a helpful message."""
    if not os.path.exists(config.TOKEN_FILE):
        print("ERROR: No token file found. Run reauth.py first.")
        sys.exit(1)
    try:
        c = auth.client_from_token_file(
            token_path=config.TOKEN_FILE,
            api_key=config.SCHWAB_APP_KEY,
            app_secret=config.SCHWAB_APP_SECRET,
        )
        return c
    except Exception as e:
        print(f"ERROR: Could not load token — {e}")
        print("The refresh token may have expired. Run reauth.py to fix this.")
        sys.exit(1)


# ── Market data fetches ───────────────────────────────────────────────────────

def fetch_quotes(c):
    """Fetch previous close + % change for SPX, VIX, SPY, QQQ."""
    tickers = ["$SPX", "$VIX", "SPY", "QQQ"]
    resp = c.get_quotes(tickers)
    resp.raise_for_status()
    raw = resp.json()

    results = {}
    ticker_map = {
        "$SPX": "spx",
        "$VIX": "vix",
        "SPY":  "spy",
        "QQQ":  "qqq",
    }

    for api_key, short_key in ticker_map.items():
        q = raw.get(api_key, {}).get("quote", {})
        close      = q.get("lastPrice") or q.get("closePrice")
        prev_close = q.get("closePrice") or q.get("lastPrice")
        open_price = q.get("openPrice")
        net_change = q.get("netChange", 0)
        # Use API pct if available, otherwise calculate from close vs open
        pct_change = q.get("netPercentChange") or q.get("markPercentChange")
        if not pct_change and prev_close and open_price and open_price != 0:
            pct_change = round((prev_close - open_price) / open_price * 100, 2)
        pct_change = pct_change or 0

        results[short_key] = {
            "close":      round(close, 2) if close else None,
            "pct_change": round(pct_change, 2),
            "net_change": round(net_change, 2),
            # color for template: green if up, red if down
            "color": "#22C55E" if pct_change >= 0 else "#CC3333",
            # VIX is inverted: VIX down = good (green for newsletter)
            "display_color": ("#22C55E" if pct_change <= 0 else "#CC3333")
                              if short_key == "vix"
                              else ("#22C55E" if pct_change >= 0 else "#CC3333"),
        }

    return results


def fetch_50day_ma(c):
    """Calculate SPX 50-day simple moving average from daily history."""
    # Try multiple ticker formats — Schwab is inconsistent between endpoints
    tickers_to_try = ["$SPX.X", "SPX", "$SPX"]

    candles = []
    for ticker in tickers_to_try:
        try:
            resp = c.get_price_history_every_day(
                ticker,
                start_datetime=datetime.combine(
                    date.today() - timedelta(days=90),
                    datetime.min.time()
                ),
                end_datetime=datetime.combine(
                    date.today(),
                    datetime.min.time()
                ),
            )
            if resp.status_code == 200:
                candles = resp.json().get("candles", [])
                if candles:
                    print(f"  SPX history fetched using ticker: {ticker}")
                    break
        except Exception:
            continue

    if not candles:
        print("  WARNING: Could not fetch SPX price history. MA will be skipped.")
        return None

    closes = [bar["close"] for bar in candles]
    if len(closes) < 50:
        print(f"  WARNING: Only {len(closes)} days of history, using available data.")
    last_50 = closes[-50:] if len(closes) >= 50 else closes
    return round(sum(last_50) / len(last_50), 2)


def fetch_0dte_chain(c, debug=False):
    """Fetch the full SPX 0DTE options chain. Returns raw chain JSON."""
    today = date.today()
    resp = c.get_option_chain("SPX", from_date=today, to_date=today)
    resp.raise_for_status()
    chain = resp.json()

    if debug:
        # Print first contract's full structure so we can verify field names
        for exp_map in ["callExpDateMap", "putExpDateMap"]:
            for exp_date, strikes in chain.get(exp_map, {}).items():
                for strike, contracts in list(strikes.items())[:1]:
                    import json as _json
                    print(f"\n-- Sample contract ({exp_map}, strike {strike}) --")
                    print(_json.dumps(contracts[0], indent=2)[:800])
                    break
                break
            break

    return chain


def fetch_0dte_volume(chain):
    """
    Derive total volume, top strikes, and 20-day average from the chain.
    Also finds The Number — the biggest intraday % winner.
    """
    today_volume   = 0
    call_volumes   = {}   # strike -> volume
    put_volumes    = {}
    the_number     = None
    best_pct       = 0

    for exp_map_key, vol_map in [
        ("callExpDateMap", call_volumes),
        ("putExpDateMap",  put_volumes),
    ]:
        for exp_date, strikes in chain.get(exp_map_key, {}).items():
            for strike_str, contracts in strikes.items():
                strike = float(strike_str)
                for contract in contracts:
                    vol = contract.get("totalVolume", 0)
                    today_volume += vol
                    vol_map[strike] = vol_map.get(strike, 0) + vol

                    # The Number: find biggest intraday % gainer
                    # Schwab returns OHLC at top level in camelCase
                    # Need meaningful open price (> $0.05) and real volume (> 50 contracts)
                    open_px = (contract.get("openPrice")
                               or contract.get("open")
                               or contract.get("day", {}).get("open", 0))
                    high_px = (contract.get("highPrice")
                               or contract.get("high")
                               or contract.get("day", {}).get("high", 0))
                    last_px = (contract.get("lastPrice")
                               or contract.get("last")
                               or contract.get("day", {}).get("last", 0))
                    day_vol = vol

                    if open_px and open_px >= 0.05 and high_px > open_px and day_vol >= 50:
                        pct = (high_px - open_px) / open_px * 100
                        if pct > best_pct:
                            best_pct = pct
                            the_number = {
                                "strike":    strike,
                                "type":      "call" if exp_map_key == "callExpDateMap" else "put",
                                "open":      open_px,
                                "high":      high_px,
                                "last":      last_px,
                                "volume":    day_vol,
                                "pct_gain":  round(pct, 0),
                                "gain_dollars": round((high_px - open_px) * 100, 0),
                            }

    avg = _get_rolling_average(today_volume)

    # Top 3 call and put strikes by volume
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
    """
    Load the last 20 days of saved market data to compute rolling avg volume.
    Falls back to today's volume if not enough history yet.
    """
    if not os.path.exists(config.MARKET_DATA_DIR):
        return today_volume

    files = sorted([
        f for f in os.listdir(config.MARKET_DATA_DIR)
        if f.endswith(".json")
    ])[-20:]  # last 20 files

    volumes = []
    for fname in files:
        path = os.path.join(config.MARKET_DATA_DIR, fname)
        with open(path) as f:
            data = json.load(f)
        vol = data.get("options", {}).get("today_volume")
        if vol:
            volumes.append(vol)

    if not volumes:
        return today_volume

    return round(sum(volumes) / len(volumes))


# ── Save ──────────────────────────────────────────────────────────────────────

def save(data):
    os.makedirs(config.MARKET_DATA_DIR, exist_ok=True)
    filename = f"{data['date']}.json"
    path = os.path.join(config.MARKET_DATA_DIR, filename)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved: {path}")
    return path


# ── Load existing file (for merge) ────────────────────────────────────────────

def load_existing(today):
    path = os.path.join(config.MARKET_DATA_DIR, f"{today}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["options", "quotes", "full"],
        default="full",
        help="options=3:50PM fetch, quotes=4:35PM fetch, full=both"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Run even on non-trading days (for testing)"
    )
    args = parser.parse_args()
    today = str(date.today())

    if not args.force and not is_trading_day():
        print(f"Today ({today}) is not a trading day. Skipping. Use --force to override.")
        sys.exit(0)

    print(f"Fetching market data [{args.mode}] for {today}...")
    c = get_client()

    # ── Phase 1: Options chain (3:50 PM ET) ───────────────────────────────────
    if args.mode in ("options", "full"):
        print("  Fetching 0DTE SPX options chain...")
        try:
            chain   = fetch_0dte_chain(c)
            options = fetch_0dte_volume(chain)
            print(f"  Options volume: {options.get('today_volume', 0):,}")
            tn = options.get("the_number")
            if tn:
                print(f"  Best 0DTE trade: SPX {tn['strike']:.0f} {tn['type'].upper()} "
                      f"{tn['open']:.2f} -> {tn['high']:.2f} (+{tn['pct_gain']:.0f}%)")
        except Exception as e:
            print(f"  WARNING: Could not fetch options chain — {e}")
            options = {
                "today_volume": None, "volume_20day_avg": None, "vs_average_pct": None,
                "call_volume": None, "put_volume": None, "put_call_ratio": None,
                "top_call_strikes": [], "top_put_strikes": [], "the_number": None,
            }

        # Merge with existing file if quotes already there (full mode),
        # or start fresh (options-only mode)
        existing = load_existing(today) or {}
        payload = {
            "date":       today,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "spx": existing.get("spx", {}),
            "vix": existing.get("vix", {}),
            "spy": existing.get("spy", {}),
            "qqq": existing.get("qqq", {}),
            "options": options,
        }
        path = save(payload)
        print(f"Options data saved to {path}")

    # ── Phase 2: Quotes + MA (4:35 PM ET) ────────────────────────────────────
    if args.mode in ("quotes", "full"):
        print("  Fetching quotes (SPX, VIX, SPY, QQQ)...")
        quotes = fetch_quotes(c)

        print("  Fetching SPX 50-day MA...")
        ma_50 = fetch_50day_ma(c)
        quotes["spx"]["ma_50"] = ma_50

        # Merge with existing file (preserves options data from phase 1)
        existing = load_existing(today) or {}
        payload = {
            "date":       today,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            **quotes,
            "options": existing.get("options", {
                "today_volume": None, "volume_20day_avg": None, "vs_average_pct": None,
                "call_volume": None, "put_volume": None, "put_call_ratio": None,
                "top_call_strikes": [], "top_put_strikes": [], "the_number": None,
            }),
        }
        path = save(payload)

        print("\n-- Market Snapshot --------------------------")
        for ticker in ["spx", "vix", "spy", "qqq"]:
            d = payload[ticker]
            arrow = "+" if (d.get("pct_change") or 0) >= 0 else "-"
            close = d.get("close")
            pct   = abs(d.get("pct_change") or 0)
            print(f"  {ticker.upper():4s}  {close:>10,.2f}  {arrow} {pct:.2f}%") if close else print(f"  {ticker.upper():4s}  unavailable")
        print(f"\n  SPX 50-day MA:  {f'{ma_50:,.2f}' if ma_50 else 'unavailable'}")
        opts = payload["options"]
        vol  = opts.get("today_volume")
        print(f"  0DTE Volume:    {f'{vol:,}' if vol is not None else 'unavailable (run --mode options before 4PM)'}")
        print(f"\nDone. Saved to {path}")


if __name__ == "__main__":
    main()
