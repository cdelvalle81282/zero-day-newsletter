"""
Zero Day Newsletter — Market Data Fetcher
Pulls previous day's closes and 0DTE SPX options volume from Schwab API.
Run after market close (4:30 PM ET) on trading days.

Cron example:
    30 16 * * 1-5  python3 /path/to/scripts/fetch_market_data.py
"""

import json
import os
import sys
from datetime import date, datetime, timedelta

from schwab import auth

import config

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


def fetch_0dte_chain(c):
    """Fetch the full SPX 0DTE options chain. Returns raw chain JSON."""
    today = date.today()
    resp = c.get_option_chain("SPX", from_date=today, to_date=today)
    resp.raise_for_status()
    return resp.json()


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
                    # Need meaningful open price (> $0.05) and real volume (> 50 contracts)
                    day       = contract.get("day", {})
                    open_px   = day.get("open") or contract.get("openPrice", 0)
                    high_px   = day.get("high") or contract.get("highPrice", 0)
                    last_px   = day.get("last") or contract.get("last", 0)
                    day_vol   = day.get("volume") or vol

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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Fetching market data for {date.today()}...")

    c = get_client()

    print("  Fetching quotes...")
    quotes = fetch_quotes(c)

    print("  Fetching SPX 50-day MA...")
    ma_50 = fetch_50day_ma(c)
    if ma_50 is not None:
        quotes["spx"]["ma_50"] = ma_50
    else:
        quotes["spx"]["ma_50"] = None

    print("  Fetching 0DTE SPX options chain...")
    try:
        chain   = fetch_0dte_chain(c)
        options = fetch_0dte_volume(chain)
    except Exception as e:
        print(f"  WARNING: Could not fetch options chain — {e}")
        print("  (This is normal after market close. Will retry next trading day.)")
        options = {
            "today_volume": None, "volume_20day_avg": None, "vs_average_pct": None,
            "call_volume": None, "put_volume": None, "put_call_ratio": None,
            "top_call_strikes": [], "top_put_strikes": [], "the_number": None,
        }

    payload = {
        "date":    str(date.today()),
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        **quotes,
        "options": options,
    }

    path = save(payload)

    # Pretty print summary
    print("\n-- Market Snapshot --------------------------")
    for ticker in ["spx", "vix", "spy", "qqq"]:
        d = payload[ticker]
        arrow = "+" if d["pct_change"] >= 0 else "-"
        print(f"  {ticker.upper():4s}  {d['close']:>10,.2f}  {arrow} {abs(d['pct_change']):.2f}%")
    print(f"\n  SPX 50-day MA:       {f'{ma_50:,.2f}' if ma_50 else 'unavailable'}")
    vol = options['today_volume']
    avg = options['volume_20day_avg']
    print(f"  0DTE Volume:         {f'{vol:,}' if vol is not None else 'unavailable'}")
    print(f"  20-day Avg Volume:   {f'{avg:,}' if avg is not None else 'unavailable'}")
    if options.get("vs_average_pct") is not None:
        print(f"  vs Average:          {options['vs_average_pct']:+.1f}%")
    if options.get("put_call_ratio") is not None:
        print(f"  Put/Call Ratio:      {options['put_call_ratio']:.2f}")
    tn = options.get("the_number")
    if tn:
        print(f"  Best 0DTE trade:     SPX {tn['strike']:.0f} {tn['type'].upper()} "
              f"{tn['open']:.2f} -> {tn['high']:.2f} (+{tn['pct_gain']:.0f}%)")
    print(f"\nDone. Saved to {path}")


if __name__ == "__main__":
    main()
