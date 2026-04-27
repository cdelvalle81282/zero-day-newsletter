"""
Zero Day Newsletter — Assembly Engine
Reads the Daily Brief JSON + market data JSON, renders the Zero Day HTML
template, and creates a draft message in OptiPub for review before send.

Usage:
    python3 scripts/assemble_newsletter.py
    python3 scripts/assemble_newsletter.py --date 2026-04-17  (override date)
    python3 scripts/assemble_newsletter.py --dry-run           (print HTML, don't post to OptiPub)
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

from html import escape as html_escape

import config
from trading_calendar import market_data_date_for_newsletter, is_trading_day

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_market_data(newsletter_date):
    """
    Load market data for the correct trading day for a given newsletter date.

    The newsletter describes the PREVIOUS trading day's market action:
      Monday newsletter  → Friday's data
      Post-holiday newsletter → last trading day before the holiday

    Falls back to the most recent available file if the exact date isn't found.
    """
    # Determine which trading day this newsletter describes
    try:
        from datetime import date as date_cls
        d = date_cls.fromisoformat(str(newsletter_date))
        data_date = str(market_data_date_for_newsletter(d))
    except Exception:
        data_date = str(newsletter_date)

    exact = os.path.join(config.MARKET_DATA_DIR, f"{data_date}.json")
    if os.path.exists(exact):
        return load_json(exact)

    # Fall back to most recent file
    files = sorted([
        f for f in os.listdir(config.MARKET_DATA_DIR)
        if f.endswith(".json")
    ])
    if not files:
        return None
    path = os.path.join(config.MARKET_DATA_DIR, files[-1])
    print(f"  WARNING: No market data for {data_date}, using {files[-1]}")
    return load_json(path)


def find_daily_brief(target_date):
    """Load Daily Brief JSON for the target date."""
    path = os.path.join(config.DAILY_BRIEF_DIR, f"{target_date}.json")
    if not os.path.exists(path):
        print(f"ERROR: No Daily Brief found for {target_date}")
        print(f"Expected: {path}")
        print("Create it with: python3 scripts/daily_brief.py")
        sys.exit(1)
    return load_json(path)


def fmt_price(val):
    """Format a price value with commas: 6782.81 → '6,782.81'"""
    if val is None:
        return "—"
    return f"{val:,.2f}"


def fmt_pct(val):
    """Format a % change: 2.51 → '+2.51%', -1.2 → '-1.20%'"""
    if val is None:
        return "—"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


def signal_config(color):
    """Return hex color, icon, and label for the signal color."""
    return {
        "green":  {"hex": "#22C55E", "icon": "&#x2713;", "label": "GREEN LIGHT"},
        "yellow": {"hex": "#D4A017", "icon": "&#x26A0;", "label": "YELLOW LIGHT"},
        "red":    {"hex": "#CC3333", "icon": "&#x2715;", "label": "RED LIGHT"},
    }.get(color.lower(), {"hex": "#D4A017", "icon": "&#x26A0;", "label": "YELLOW LIGHT"})


def signal_key_html(active_color):
    """Build the 3-row signal legend, highlighting the active color."""
    items = [
        ("green",  "#22C55E", "GREEN",  "Clear market bias. Look to trade in the direction of the trend."),
        ("yellow", "#D4A017", "YELLOW", "Transitional market bias. Mixed signals; size down and wait for confirmation."),
        ("red",    "#CC3333", "RED",    "No clear market bias. Narrow range or no trend; high-probability plays are harder to find."),
    ]
    rows = []
    for color, hex_color, label, desc in items:
        is_active = color == active_color.lower()
        bg     = "#FAFAFA" if not is_active else {"green": "#F0FFF4", "yellow": "#FFFBEB", "red": "#FFF0F0"}.get(color, "#FAFAFA")
        border = f"border-left:3px solid {hex_color};" if is_active else "border-left:3px solid transparent;"
        badge  = (
            f' <span style="display:inline-block;padding:1px 5px;background-color:{hex_color};'
            f'color:#FFFFFF;font-family:Tahoma,Geneva,Verdana,sans-serif;font-size:9px;'
            f'font-weight:bold;letter-spacing:1px;vertical-align:middle;">TODAY</span>'
        ) if is_active else ""
        rows.append(
            f'<tr>'
            f'<td style="padding:8px 12px;background-color:{bg};border-bottom:1px solid #F0F0F0;{border}">'
            f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;'
            f'background-color:{hex_color};margin-right:6px;vertical-align:middle;"></span>'
            f'<span style="font-family:Tahoma,Geneva,Verdana,sans-serif;font-size:11px;'
            f'font-weight:bold;color:{hex_color};">{label}</span>'
            f'{badge}'
            f'<span style="font-family:Tahoma,Geneva,Verdana,sans-serif;font-size:11px;'
            f'color:#2A2A2A;">: {desc}</span>'
            f'</td>'
            f'</tr>'
        )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%"'
        ' style="margin-top:14px;border:1px solid #E5E7EB;">'
        + ''.join(rows)
        + '</table>'
    )


def format_date_long(d):
    """2026-04-17 → 'Friday, April 17, 2026'"""
    dt = datetime.strptime(str(d), "%Y-%m-%d")
    return dt.strftime("%A, %B %-d, %Y") if os.name != "nt" else dt.strftime("%A, %B {d}, %Y").replace("{d}", str(dt.day))


def format_date_short(d):
    """2026-04-17 → 'Friday, April 17'"""
    dt = datetime.strptime(str(d), "%Y-%m-%d")
    return dt.strftime("%A, %B %-d") if os.name != "nt" else dt.strftime("%A, %B {d}").replace("{d}", str(dt.day))


def fmt_volume(v):
    """Format volume: 2800000 → '2.8M', 280000 → '280K'"""
    if v is None:
        return "N/A"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return str(v)


def generate_the_number(tn):
    """
    Auto-generate The Number value and text from the best 0DTE trade of the day.
    tn is the 'the_number' dict from market data.
    """
    if not tn:
        return None, None

    pct   = int(tn["pct_gain"])
    gain  = int(tn["gain_dollars"])
    strike = int(tn["strike"])
    kind   = tn["type"].lower()
    open_  = tn["open"]
    high_  = tn["high"]

    number_value = f"+{pct:,}%"
    number_text  = (
        f"An SPX {strike:,} {kind} expiring today opened at ${open_:.2f}. "
        f"By the high of the day it traded at ${high_:.2f}, "
        f"a ${gain:,} gain per contract ({pct:,}%) for traders who caught the move."
    )
    return number_value, number_text


def generate_volume_anomaly(options):
    """
    Auto-generate Volume Anomaly headline and narrative from options data.
    """
    vol     = options.get("today_volume")
    avg     = options.get("volume_20day_avg")
    vs_pct  = options.get("vs_average_pct")
    pc      = options.get("put_call_ratio")
    top_calls = options.get("top_call_strikes", [])
    top_puts  = options.get("top_put_strikes", [])
    call_vol  = options.get("call_volume", 0) or 0
    put_vol   = options.get("put_volume", 0) or 0

    if not vol:
        return None, None

    headline = f"SPX 0DTE Volume: {fmt_volume(vol)} Contracts"

    # Build narrative
    parts = []

    # Opening line — volume vs average
    if vs_pct is not None:
        direction = "above" if vs_pct >= 0 else "below"
        parts.append(
            f"Today's 0DTE SPX volume came in at {fmt_volume(vol)} contracts, "
            f"roughly {abs(vs_pct):.0f}% {direction} the 20-day average of {fmt_volume(avg)}."
        )
    else:
        parts.append(f"Today's 0DTE SPX volume came in at {fmt_volume(vol)} contracts.")

    # Call vs put skew
    if call_vol and put_vol:
        skew = abs(call_vol - put_vol) / max(call_vol, put_vol)
        if skew >= 0.10:
            dominant = "call" if call_vol > put_vol else "put"
            parts.append(
                f"{dominant.capitalize()} activity dominated, "
                f"with {fmt_volume(call_vol)} calls vs {fmt_volume(put_vol)} puts."
            )
        else:
            parts.append(
                f"Call and put activity were roughly balanced, "
                f"with {fmt_volume(call_vol)} calls vs {fmt_volume(put_vol)} puts."
            )

    # Top call strikes
    if top_calls:
        strikes_str = ", ".join(
            f"{int(s['strike']):,}" for s in top_calls
        )
        parts.append(
            f"The heaviest call volume concentrated at the {strikes_str} strikes."
        )

    # Top put strikes
    if top_puts:
        strikes_str = ", ".join(
            f"{int(s['strike']):,}" for s in top_puts
        )
        parts.append(
            f"On the put side, the {strikes_str} strikes saw the most action."
        )

    # Put/call ratio context
    if pc is not None:
        if pc < 0.7:
            sentiment = "reflecting a strongly bullish lean from options traders"
        elif pc < 0.9:
            sentiment = "a modestly bullish tone in the options market"
        elif pc < 1.1:
            sentiment = "a fairly neutral read from the options market"
        else:
            sentiment = "a defensive tilt from options traders"
        parts.append(f"The put/call ratio finished at {pc:.2f}, {sentiment}.")

    return headline, " ".join(parts)


# ── Token substitution ────────────────────────────────────────────────────────

def build_tokens(brief, market, target_date):
    """Build the full token → value map from Daily Brief + market data."""
    sig     = signal_config(brief.get("signal_color", "yellow"))
    spx     = market.get("spx", {})
    vix     = market.get("vix", {})
    spy     = market.get("spy", {})
    qqq     = market.get("qqq", {})
    options = market.get("options", {})

    # Author
    author_key  = brief.get("author", config.DEFAULT_AUTHOR)
    author_data = config.AUTHORS.get(author_key, config.AUTHORS[config.DEFAULT_AUTHOR])

    # Auto-generate The Number from market data; fall back to brief if unavailable
    auto_number_value, auto_number_text = generate_the_number(options.get("the_number"))
    the_number_value = auto_number_value or brief.get("the_number_value", "")
    the_number_text  = auto_number_text  or brief.get("the_number_text", "")
    if auto_number_value:
        print("  The Number: auto-generated from options chain data.")
    else:
        print("  The Number: using Daily Brief value (options data unavailable).")

    # Auto-generate Volume Anomaly; fall back to brief if unavailable
    auto_vol_headline, auto_vol_text = generate_volume_anomaly(options)
    volume_headline = auto_vol_headline or brief.get("volume_anomaly_headline", "")
    volume_text     = auto_vol_text     or brief.get("volume_anomaly_text", "")
    if auto_vol_headline:
        print("  Volume Anomaly: auto-generated from options chain data.")
    else:
        print("  Volume Anomaly: using Daily Brief value (options data unavailable).")

    editorial_url = brief.get("editorial_url", "").strip()
    if editorial_url and re.match(r'^https?://', editorial_url):
        editorial_link_html = (
            f'<p style="margin: 10px 0 0 0;">'
            f'<a href="{html_escape(editorial_url)}" style="color: #3B82F6; '
            f'font-family: Tahoma, Geneva, Verdana, sans-serif; font-size: 14px; '
            f'text-decoration: none;">Read full editorial &#8594;</a></p>'
        )
    else:
        editorial_link_html = ""

    return {
        # Date
        "ISSUE_DATE_LONG":  format_date_long(target_date),
        "LEVELS_DATE":      format_date_short(target_date),

        # Author
        "AUTHOR_NAME":  author_data["name"],
        "AUTHOR_TITLE": author_data["title"],
        "AUTHOR_PHOTO": author_data["photo"],

        # Signal
        "SIGNAL_COLOR_HEX":   sig["hex"],
        "SIGNAL_ICON":        sig["icon"],
        "SIGNAL_LABEL":       sig["label"],
        "SIGNAL_TEXT":        html_escape(brief.get("signal_text", "")),
        "SIGNAL_ATTRIBUTION": html_escape(author_data["name"]),
        "SIGNAL_KEY_HTML":    signal_key_html(brief.get("signal_color", "yellow")),

        # Levels (labels are user text, values are formatted numbers — both escaped)
        "LEVEL_R2_LABEL":  html_escape(brief.get("level_resistance_2_label", "Resistance 2")),
        "LEVEL_R2_VALUE":  fmt_price(brief.get("level_resistance_2_value")),
        "LEVEL_R1_LABEL":  html_escape(brief.get("level_resistance_1_label", "Resistance 1")),
        "LEVEL_R1_VALUE":  fmt_price(brief.get("level_resistance_1_value")),
        "LEVEL_KEY_LABEL": html_escape(brief.get("level_key_label", "Premarket Price")),
        "LEVEL_KEY_VALUE": fmt_price(brief.get("level_key_value")),
        "LEVEL_S1_LABEL":  html_escape(brief.get("level_support_1_label", "Support 1")),
        "LEVEL_S1_VALUE":  fmt_price(brief.get("level_support_1_value")),
        "LEVEL_S2_LABEL":  html_escape(brief.get("level_support_2_label", "Support 2")),
        "LEVEL_S2_VALUE":  fmt_price(brief.get("level_support_2_value")),
        "LEVELS_NOTE":     html_escape(brief.get("levels_note", "")),

        # The Number — auto from options chain, fallback to brief
        "THE_NUMBER":      html_escape(the_number_value) if the_number_value else "",
        "THE_NUMBER_TEXT": html_escape(the_number_text) if the_number_text else "",

        # Volume Anomaly — auto from options chain, fallback to brief
        "VOLUME_HEADLINE": html_escape(volume_headline) if volume_headline else "",
        "VOLUME_TEXT":     html_escape(volume_text) if volume_text else "",

        # Editor's Note — always from brief
        "EDITOR_NOTE_TEXT": html_escape(brief.get("editor_note_text", "")),
        "EDITORIAL_LINK_HTML": editorial_link_html,

        # CTA block — filled from daily brief, carries forward day to day
        "CTA_HEADLINE":    html_escape(brief.get("cta_headline",    "Trade 0DTE With the Pros")),
        "CTA_BODY":        html_escape(brief.get("cta_body",        "Join the Option Pit live trading room for real-time 0DTE setups, levels, and alerts from Mark, Licia, and the team.")),
        "CTA_URL":         html_escape(brief.get("cta_url",         "https://optionpit.com")),
        "CTA_BUTTON_TEXT": html_escape(brief.get("cta_button_text", "Learn More")),

        # Market Snapshot
        "SNAP_SPX_VALUE":  fmt_price(spx.get("close")),
        "SNAP_SPX_PCT":    fmt_pct(spx.get("pct_change")),
        "SNAP_SPX_COLOR":  spx.get("display_color", "#22C55E"),
        "SNAP_VIX_VALUE":  fmt_price(vix.get("close")),
        "SNAP_VIX_PCT":    fmt_pct(vix.get("pct_change")),
        "SNAP_VIX_COLOR":  vix.get("display_color", "#22C55E"),
        "SNAP_SPY_VALUE":  fmt_price(spy.get("close")),
        "SNAP_SPY_PCT":    fmt_pct(spy.get("pct_change")),
        "SNAP_SPY_COLOR":  spy.get("display_color", "#22C55E"),
        "SNAP_QQQ_VALUE":  fmt_price(qqq.get("close")),
        "SNAP_QQQ_PCT":    fmt_pct(qqq.get("pct_change")),
        "SNAP_QQQ_COLOR":  qqq.get("display_color", "#22C55E"),
    }


def render_template(tokens):
    """Load the HTML template and substitute all {{TOKEN}} placeholders."""
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "TheZeroDay_Sample_Issue_3.html"
    )
    if not os.path.exists(template_path):
        print(f"ERROR: Template not found at {template_path}")
        sys.exit(1)

    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    for token, value in tokens.items():
        html = html.replace("{{" + token + "}}", str(value) if value is not None else "")

    # Warn about any remaining unfilled tokens
    remaining = re.findall(r"\{\{([A-Z_]+)\}\}", html)
    if remaining:
        print(f"  WARNING: Unfilled tokens in template: {remaining}")

    return html


# ── OptiPub draft creation ────────────────────────────────────────────────────

def create_optipub_draft(html, brief, target_date,
                         included_segments=None, excluded_segments=None):
    """POST the rendered HTML as a draft message to OptiPub."""
    import urllib.request

    signal_label = signal_config(brief.get("signal_color", "yellow"))["label"]
    dt = datetime.strptime(str(target_date), "%Y-%m-%d")
    date_str = dt.strftime("%B %-d") if os.name != "nt" else dt.strftime("%B {d}").replace("{d}", str(dt.day))
    title = f"0DTE Daily — {signal_label} — {date_str}"

    body = {
        "publication_id":   config.ZERO_DAY_PUBLICATION_ID,
        "message_type_id":  3,   # email-free-style
        "sender_id":        config.OPTIPUB_SENDER_ID,
        "title":            title,
        "content":          html,
    }

    if included_segments:
        body["included_segments"] = [{"id": s} for s in included_segments]
    if excluded_segments:
        body["excluded_segments"] = [{"id": s} for s in excluded_segments]

    payload = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        f"{config.OPTIPUB_API_BASE}/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.OPTIPUB_API_KEY}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())

    return result.get("data", {}).get("id"), title


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--dry-run", action="store_true",
                        help="Render HTML and print it without posting to OptiPub")
    args = parser.parse_args()

    target_date = args.date
    print(f"Assembling Zero Day for {target_date}...")

    print("  Loading Daily Brief...")
    brief = find_daily_brief(target_date)

    print("  Loading market data...")
    market = find_market_data(target_date)
    if not market:
        print("ERROR: No market data available. Run fetch_market_data.py first.")
        sys.exit(1)

    print("  Building tokens...")
    tokens = build_tokens(brief, market, target_date)

    print("  Rendering template...")
    html = render_template(tokens)

    if args.dry_run:
        out_path = f"zero_day_draft_{target_date}.html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\nDry run complete. Draft saved to: {out_path}")
        print("Open it in a browser to preview.")
        return

    print("  Creating OptiPub draft...")
    msg_id, title = create_optipub_draft(html, brief, target_date)
    print(f"\nDraft created in OptiPub:")
    print(f"  ID:    {msg_id}")
    print(f"  Title: {title}")
    print(f"  Pub:   {config.ZERO_DAY_PUBLICATION_ID} (LLF)")
    print("\nReview and schedule it in OptiPub before sending.")


if __name__ == "__main__":
    main()
