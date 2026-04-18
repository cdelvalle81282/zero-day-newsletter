"""
Zero Day Newsletter — Market Data Fetcher

Two-phase fetch to work around Schwab's options chain expiry at 4 PM ET:

  Phase 1 — options (run at 3:50 PM ET, while 0DTE contracts still active):
      python3 scripts/fetch_market_data.py --mode options

  Phase 2 — quotes (run at 4:35 PM ET, after market close):
      python3 scripts/fetch_market_data.py --mode quotes

  Full fetch (both phases at once, useful for testing):
      python3 scripts/fetch_market_data.py

  Backfill a past date (patches pct_change from candle history):
      python3 scripts/fetch_market_data.py --mode quotes --date 2026-04-16 --force
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

def _pct_from_candles(c, tickers_to_try, target_date):
    """
    Derive daily pct_change from price history candles for target_date.
    More reliable than the quote API, which resets netPercentChange overnight.
    Returns float or None.
    """
    from datetime import timezone
    start = target_date - timedelta(days=7)
    end   = target_date + timedelta(days=1)
    for ticker in tickers_to_try:
        try:
            resp = c.get_price_history_every_day(
                ticker,
                start_datetime=datetime.combine(start, datetime.min.time()),
                end_datetime=datetime.combine(end,   datetime.min.time()),
            )
            if resp.status_code != 200:
                continue
            candles = resp.json().get("candles", [])
            # Map each candle to its calendar date
            dated = {}
            for bar in candles:
                bar_date = datetime.fromtimestamp(bar["datetime"] / 1000, tz=timezone.utc).date()
                dated[bar_date] = bar["close"]
            if target_date not in dated:
                continue
            prev_dates = sorted(d for d in dated if d < target_date)
            if not prev_dates:
                continue
            prev_close  = dated[prev_dates[-1]]
            today_close = dated[target_date]
            if prev_close:
                return round((today_close - prev_close) / prev_close * 100, 2)
        except Exception:
            continue
    return None


def fetch_quotes(c, pct_overrides=None):
    """
    Fetch closing quotes for SPX, VIX, SPY, QQQ.
    pct_overrides: dict of short_key -> pct_change to use when the quote API
    returns 0 (e.g. indices fetched after hours). Derived from candle history.
    """
    tickers = ["$SPX", "$VIX", "SPY", "QQQ"]
    resp = c.get_quotes(tickers)
    resp.raise_for_status()
    raw = resp.json()
    pct_overrides = pct_overrides or {}

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
        net_change = q.get("netChange") or 0

        # netPercentChange resets to 0 for indices after hours — check None explicitly
        api_pct = q.get("netPercentChange")
        if api_pct is None:
            api_pct = q.get("markPercentChange")

        if api_pct is not None and api_pct != 0:
            pct_change = api_pct
        elif net_change and close and (close - net_change) != 0:
            # Derive from net_change: pct = net / prev_close
            pct_change = round(net_change / (close - net_change) * 100, 2)
        else:
            # Fall back to candle-derived value (reliable for past dates / off-hours)
            pct_change = pct_overrides.get(short_key) or 0

        # If API pct was 0 but candle override exists, prefer candle
        if pct_change == 0 and short_key in pct_overrides and pct_overrides[short_key] is not None:
            pct_change = pct_overrides[short_key]

        results[short_key] = {
            "close":      round(close, 2) if close else None,
            "pct_change": round(pct_change, 2),
            "net_change": round(net_change, 2),
            "color": "#22C55E" if pct_change >= 0 else "#CC3333",
            "display_color": ("#22C55E" if pct_change <= 0 else "#CC3333")
                              if short_key == "vix"
                              else ("#22C55E" if pct_change >= 0 else "#CC3333"),
        }

    return results


def fetch_spx_history(c, target_date=None):
    """
    Fetch SPX daily history. Returns (ma_50, pct_change).
    pct_change is derived from the last two candles — reliable even off-hours.
    """
    target_date = target_date or date.today()
    tickers_to_try = ["$SPX.X", "SPX", "$SPX"]

    candles = []
    for ticker in tickers_to_try:
        try:
            resp = c.get_price_history_every_day(
                ticker,
                start_datetime=datetime.combine(
                    target_date - timedelta(days=90),
                    datetime.min.time()
                ),
                end_datetime=datetime.combine(
                    target_date + timedelta(days=1),
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
        print("  WARNING: Could not fetch SPX price history. MA and pct will be skipped.")
        return None, None

    closes = [bar["close"] for bar in candles]
    if len(closes) < 50:
        print(f"  WARNING: Only {len(closes)} days of history, using available data.")
    last_50 = closes[-50:] if len(closes) >= 50 else closes
    ma_50 = round(sum(last_50) / len(last_50), 2)

    pct_change = None
    if len(closes) >= 2:
        prev_c  = closes[-2]
        today_c = closes[-1]
        if prev_c:
            pct_change = round((today_c - prev_c) / prev_c * 100, 2)

    return ma_50, pct_change


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
                    print(f"\n-- Sample contract ({exp_map}, strike {strike}) --")
                    print(json.dumps(contracts[0], indent=2)[:800])
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
    return {}


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
    parser.add_argument(
        "--date",
        default=None,
        help="Target date YYYY-MM-DD (default: today). Use with --force to backfill past dates."
    )
    args = parser.parse_args()

    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"ERROR: Invalid --date format. Use YYYY-MM-DD.")
            sys.exit(1)
    else:
        target_date = date.today()
    today = str(target_date)
    is_past = target_date < date.today()

    if not args.force and not is_trading_day(target_date):
        print(f"{today} is not a trading day. Skipping. Use --force to override.")
        sys.exit(0)

    print(f"Fetching market data [{args.mode}] for {today}{'  (backfill)' if is_past else ''}...")
    c = get_client()

    # ── Phase 1: Options chain (3:50 PM ET) ───────────────────────────────────
    if args.mode in ("options", "full"):
        if is_past:
            print("  Skipping options chain for past dates (0DTE contracts expired).")
            options = load_existing(today).get("options", {
                "today_volume": None, "volume_20day_avg": None, "vs_average_pct": None,
                "call_volume": None, "put_volume": None, "put_call_ratio": None,
                "top_call_strikes": [], "top_put_strikes": [], "the_number": None,
            })
        else:
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
                if "invalid_client" in str(e) or "Unauthorized" in str(e) or "OAuthError" in type(e).__name__:
                    print(f"ERROR: Schwab authentication failed — {e}")
                    sys.exit(1)
                print(f"  WARNING: Could not fetch options chain — {e}")
                options = {
                    "today_volume": None, "volume_20day_avg": None, "vs_average_pct": None,
                    "call_volume": None, "put_volume": None, "put_call_ratio": None,
                    "top_call_strikes": [], "top_put_strikes": [], "the_number": None,
                }

        existing = load_existing(today)
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
        print("  Fetching SPX history (MA + pct_change from candles)...")
        ma_50, spx_pct = fetch_spx_history(c, target_date)

        print("  Fetching VIX pct_change from candle history...")
        vix_pct = _pct_from_candles(c, ["$VIX.X", "VIX", "$VIX"], target_date)
        if vix_pct is not None:
            print(f"  VIX pct from candles: {vix_pct:+.2f}%")
        else:
            print("  WARNING: Could not derive VIX pct from candle history.")

        pct_overrides = {"spx": spx_pct, "vix": vix_pct}

        print("  Fetching quotes (SPX, VIX, SPY, QQQ)...")
        quotes = fetch_quotes(c, pct_overrides=pct_overrides)
        quotes["spx"]["ma_50"] = ma_50

        if args.mode == "full":
            existing = {}
        else:
            existing = load_existing(today)
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
