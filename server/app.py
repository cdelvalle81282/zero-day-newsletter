"""
0DTE Daily — Server
Flask app that serves the dashboard, Daily Brief form, preview, and approval workflow.

Routes:
  GET  /0dte-daily/                  Dashboard
  GET  /0dte-daily/brief/            Daily Brief form (Licia)
  POST /0dte-daily/submit            Save brief + run assembly + notify
  GET  /0dte-daily/preview/<date>    Review rendered newsletter
  POST /0dte-daily/approve/<date>    Post draft to OptiPub
  POST /0dte-daily/rerender/<date>   Re-run assembly (e.g. after market data updates)
  GET  /0dte-daily/status            JSON status of today's pipeline
"""

import json
import os
import re
import sys
import subprocess
import urllib.request
from datetime import date, datetime
from functools import wraps
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify,
    send_from_directory, abort, Response
)
from apscheduler.schedulers.background import BackgroundScheduler

# ── Path setup ────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent.parent.resolve()
SCRIPTS_DIR = BASE_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import config
from trading_calendar import is_trading_day, market_data_date_for_newsletter
from assemble_newsletter import signal_config, fmt_volume

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.urandom(32)

DRAFTS_DIR = BASE_DIR / "drafts"
DRAFTS_DIR.mkdir(exist_ok=True)
CHARTS_DIR = DRAFTS_DIR / "charts"
CHARTS_DIR.mkdir(exist_ok=True)

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

BRIEF_ALLOWED_KEYS = {
    "date", "created_at", "status",
    "signal_color", "signal_text", "signal_attribution",
    "author",
    "level_resistance_2_label", "level_resistance_2_value",
    "level_resistance_1_label", "level_resistance_1_value",
    "level_key_label", "level_key_value",
    "level_support_1_label", "level_support_1_value",
    "level_support_2_label", "level_support_2_value",
    "levels_note", "levels_chart_url",
    "the_number_value", "the_number_text",
    "volume_anomaly_headline", "volume_anomaly_text",
    "editor_note_text",
    "editorial_url",
}

NUMERIC_LEVEL_KEYS = {
    "level_resistance_2_value", "level_resistance_1_value",
    "level_key_value", "level_support_1_value", "level_support_2_value",
}

VALID_SIGNAL_COLORS = {"green", "yellow", "red"}
MAX_TEXT_LEN = 5000


# ── Security helpers ──────────────────────────────────────────────────────────

def validate_date(d):
    """Reject any target_date that isn't a real YYYY-MM-DD calendar date."""
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except (ValueError, TypeError):
        abort(400, "Invalid date format")


def validate_brief(brief):
    """Validate brief JSON keys, types, and values. Returns error string or None."""
    if not isinstance(brief, dict):
        return "Brief must be a JSON object"

    extra = set(brief.keys()) - BRIEF_ALLOWED_KEYS
    if extra:
        return f"Unknown keys: {', '.join(sorted(extra))}"

    color = brief.get("signal_color")
    if color and color not in VALID_SIGNAL_COLORS:
        return f"Invalid signal_color: {color}"

    author = brief.get("author")
    if author and author not in config.AUTHORS:
        return f"Invalid author: {author}"

    d = brief.get("date", "")
    if d:
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except (ValueError, TypeError):
            return "Invalid date format in brief"

    for url_key in ("editorial_url", "levels_chart_url"):
        url_val = brief.get(url_key)
        if url_val and not re.match(r'^https?://', url_val):
            return f"{url_key} must start with http:// or https://"

    for key, val in brief.items():
        if key in NUMERIC_LEVEL_KEYS:
            if val is not None and not isinstance(val, (int, float)):
                return f"{key} must be numeric or null"
        elif key in ("created_at", "date", "status", "signal_color", "author") or \
                key.endswith(("_text", "_label", "_note", "_attribution", "_headline", "_value", "_url")):
            if val is not None and not isinstance(val, str):
                return f"{key} must be a string"
            if isinstance(val, str) and len(val) > MAX_TEXT_LEN:
                return f"{key} exceeds {MAX_TEXT_LEN} character limit"

    return None


# ── Basic auth ────────────────────────────────────────────────────────────────

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        password = config.ZERODAY_PASSWORD
        if not password:
            return Response("Server misconfigured: auth password not set.", 503)
        auth = request.authorization
        if not auth or auth.password != password:
            return Response(
                "Authentication required.",
                401,
                {"WWW-Authenticate": 'Basic realm="0DTE Daily"'}
            )
        return f(*args, **kwargs)
    return decorated


# ── CSRF + security headers ──────────────────────────────────────────────────

@app.before_request
def enforce_json_content_type():
    """Reject non-JSON POST requests — prevents cross-origin form-based CSRF."""
    if request.method == "POST" and request.path.startswith("/0dte-daily/"):
        if request.endpoint == "upload_chart":
            return  # multipart image upload, exempt from JSON requirement
        ct = request.content_type or ""
        if "application/json" not in ct:
            return jsonify({"ok": False, "error": "Content-Type must be application/json"}), 415


@app.after_request
def add_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "frame-src 'self'; "
        "img-src 'self' https://optionpit.com https://*.optionpit.com"
    )
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ── Subprocess helpers ────────────────────────────────────────────────────────

def _subprocess_env():
    """Return an env dict that suppresses known benign deprecation warnings."""
    env = os.environ.copy()
    existing = env.get("PYTHONWARNINGS", "")
    suppress = "ignore::DeprecationWarning:authlib"
    env["PYTHONWARNINGS"] = f"{existing},{suppress}" if existing else suppress
    return env


def _subprocess_error(result):
    """Combine stderr + stdout into a single error string for reporting."""
    parts = [result.stderr.strip(), result.stdout.strip()]
    return "\n".join(p for p in parts if p) or "unknown error"


# ── Assembly helper ───────────────────────────────────────────────────────────

def run_assembly(target_date):
    """Run the assembly engine. Returns (html_path, error_message)."""
    try:
        from assemble_newsletter import (
            find_daily_brief, find_market_data,
            build_tokens, render_template as render_nl
        )
        brief  = find_daily_brief(target_date)
        market = find_market_data(target_date)
        if not market:
            return None, "No market data available for this date."
        tokens = build_tokens(brief, market, target_date)
        html   = render_nl(tokens)

        out_path = DRAFTS_DIR / f"{target_date}.html"
        out_path.write_text(html, encoding="utf-8")
        return str(out_path), None
    except SystemExit as e:
        return None, f"Assembly failed (exit {e.code})"
    except Exception:
        app.logger.exception("assembly error")
        return None, "Assembly failed unexpectedly"


# ── Notifications ─────────────────────────────────────────────────────────────

def _send_slack(text):
    """Send a Slack webhook message. Logs a warning on failure; no-ops if no webhook set."""
    webhook = config.SLACK_WEBHOOK_URL
    if not webhook:
        return
    payload = json.dumps({"text": text}).encode()
    try:
        req = urllib.request.Request(
            webhook, data=payload,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        app.logger.warning(f"Slack notification failed: {e}")


def notify_preview_ready(target_date, signal_color="yellow"):
    preview_url = f"{config.SERVER_BASE_URL}/0dte-daily/preview/{target_date}"
    color_emoji = {"green": ":large_green_circle:", "red": ":red_circle:"}.get(
        signal_color, ":large_yellow_circle:"
    )
    _send_slack(
        f"{color_emoji} *0DTE Daily draft is ready for review*\n"
        f"Date: {target_date}\n"
        f"<{preview_url}|Click here to preview and approve>"
    )


def notify_fetch_failed(job_name, error_detail):
    _send_slack(
        f":rotating_light: *0DTE Daily — market data fetch failed*\n"
        f"Job: `{job_name}`\n"
        f"```{error_detail[:500]}```"
    )


def notify_deliverability_issue(target_date, description):
    _send_slack(
        f":warning: *0DTE Daily — deliverability issue* ({target_date})\n"
        f"{description}\nCheck server logs."
    )


def notify_approved(target_date, msg_id, title):
    _send_slack(
        f":white_check_mark: *0DTE Daily approved and posted to OptiPub*\n"
        f"Date: {target_date} | Message ID: {msg_id}\nTitle: {title}"
    )


# ── Dashboard data builder ────────────────────────────────────────────────────

def _fmt_time(iso):
    try:
        from datetime import timezone
        import zoneinfo
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        et = dt.astimezone(zoneinfo.ZoneInfo("America/New_York"))
        return et.strftime("%-I:%M %p ET")
    except Exception:
        return iso[:16] if iso else ""


def build_dashboard_data(target_date):
    brief_path    = BASE_DIR / config.DAILY_BRIEF_DIR / f"{target_date}.json"
    market_path   = BASE_DIR / config.MARKET_DATA_DIR / f"{target_date}.json"
    draft_path    = DRAFTS_DIR / f"{target_date}.html"
    approved_path = DRAFTS_DIR / f"{target_date}.approved"

    brief_exists    = brief_path.exists()
    market_exists   = market_path.exists()
    draft_exists    = draft_path.exists()
    approved_exists = approved_path.exists()

    brief         = json.loads(brief_path.read_text())    if brief_exists    else {}
    approved_data = json.loads(approved_path.read_text()) if approved_exists else {}

    from datetime import date as date_cls
    d = date_cls.fromisoformat(target_date)
    prev_trading_day = str(market_data_date_for_newsletter(d))
    prev_market_path = BASE_DIR / config.MARKET_DATA_DIR / f"{prev_trading_day}.json"

    market = {}
    market_file_date = None
    if market_exists:
        candidate = json.loads(market_path.read_text())
        if candidate.get("spx", {}).get("close"):  # quotes phase has run
            market = candidate
            market_file_date = target_date
    if not market_file_date and prev_market_path.exists():
        market = json.loads(prev_market_path.read_text())
        market_file_date = prev_trading_day
    if not market_file_date:
        market_dir = BASE_DIR / config.MARKET_DATA_DIR
        if market_dir.exists():
            files = sorted(market_dir.glob("*.json"), reverse=True)
            if files:
                market = json.loads(files[0].read_text())
                market_file_date = files[0].stem

    signal_color = brief.get("signal_color", "")
    author_key   = brief.get("author", config.DEFAULT_AUTHOR)
    author_data  = config.AUTHORS.get(author_key, config.AUTHORS[config.DEFAULT_AUTHOR])

    levels = {
        "r2_label":  brief.get("level_resistance_2_label", "Resistance 2"),
        "r2_value":  f"{brief.get('level_resistance_2_value', ''):,.2f}" if brief.get("level_resistance_2_value") else "—",
        "r1_label":  brief.get("level_resistance_1_label", "Resistance 1"),
        "r1_value":  f"{brief.get('level_resistance_1_value', ''):,.2f}" if brief.get("level_resistance_1_value") else "—",
        "key_label": brief.get("level_key_label", "Premarket Price"),
        "key_value": f"{brief.get('level_key_value', ''):,.2f}" if brief.get("level_key_value") else "—",
        "s1_label":  brief.get("level_support_1_label", "Support 1"),
        "s1_value":  f"{brief.get('level_support_1_value', ''):,.2f}" if brief.get("level_support_1_value") else "—",
        "s2_label":  brief.get("level_support_2_label", "Support 2"),
        "s2_value":  f"{brief.get('level_support_2_value', ''):,.2f}" if brief.get("level_support_2_value") else "—",
    }

    tickers = []
    for key, name in [("spx","SPX"),("vix","VIX"),("spy","SPY"),("qqq","QQQ")]:
        d = market.get(key, {})
        close = d.get("close")
        pct   = d.get("pct_change", 0) or 0
        is_good = (pct <= 0) if key == "vix" else (pct >= 0)
        tickers.append({
            "name":        name,
            "close":       f"{close:,.2f}" if close else "—",
            "pct":         f"{'+' if pct >= 0 else ''}{pct:.2f}%" if close else "—",
            "color_class": "up" if is_good else "down" if close else "neutral",
        })

    opts = market.get("options", {})
    vol  = opts.get("today_volume")
    vs   = opts.get("vs_average_pct")

    tn = opts.get("the_number")
    the_number = None
    the_number_text = None
    if tn:
        pct = int(tn.get("pct_gain", 0))
        the_number = f"+{pct:,}%"
        the_number_text = (
            f"SPX {int(tn['strike']):,} {tn['type'].upper()} opened at "
            f"${tn['open']:.2f}, hit ${tn['high']:.2f}, "
            f"${int(tn['gain_dollars']):,} per contract."
        )

    token_info = {"exists": bool(config.POLYGON_API_KEY)}

    today_data = {
        "date":          target_date,
        "brief":                brief_exists,
        "market_data":          market_exists,
        "market_data_pending":  brief_exists and not market_exists,
        "draft":         draft_exists,
        "approved":      approved_exists,
        "signal_color":  signal_color,
        "signal_label":  signal_config(signal_color)["label"] if signal_color else "",
        "signal_text":   brief.get("signal_text", ""),
        "author_name":   author_data["name"],
        "levels":        levels,
        "tickers":       tickers,
        "spx_ma":        f"{market.get('spx',{}).get('ma_50'):,.2f}" if market.get("spx", {}).get("ma_50") else None,
        "options_volume": fmt_volume(vol),
        "options_vs_avg": f"{vs:+.0f}%" if vs is not None else None,
        "options_vs_avg_pos": (vs or 0) >= 0,
        "the_number":      the_number,
        "the_number_text": the_number_text,
        "market_date":   market_file_date,
        "optipub_id":    approved_data.get("msg_id"),
        "brief_time":    _fmt_time(brief.get("created_at", ""))  if brief_exists else None,
        "market_fetch_time": _fmt_time(market.get("fetched_at", "")) if market_exists else None,
        "draft_time":    None,
        "approved_time": _fmt_time(approved_data.get("approved_at", "")) if approved_exists else None,
    }

    all_dates = set()
    for d in [config.DAILY_BRIEF_DIR, config.MARKET_DATA_DIR]:
        p = BASE_DIR / d
        if p.exists():
            all_dates.update(f.stem for f in p.glob("*.json") if DATE_RE.fullmatch(f.stem))
    all_dates.update(f.stem for f in DRAFTS_DIR.glob("*.html") if DATE_RE.fullmatch(f.stem))

    history = []
    for d in sorted(all_dates, reverse=True)[:10]:
        if d == target_date:
            continue
        bp = BASE_DIR / config.DAILY_BRIEF_DIR / f"{d}.json"
        mp = BASE_DIR / config.MARKET_DATA_DIR  / f"{d}.json"
        dp = DRAFTS_DIR / f"{d}.html"
        ap = DRAFTS_DIR / f"{d}.approved"
        b  = json.loads(bp.read_text()) if bp.exists() else {}
        history.append({
            "date":         d,
            "brief":        bp.exists(),
            "market_data":  mp.exists(),
            "draft":        dp.exists(),
            "approved":     ap.exists(),
            "signal_color": b.get("signal_color", ""),
        })

    return today_data, history, token_info


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/0dte-daily/")
@require_auth
def dashboard():
    target_date = str(date.today())
    today, history, token = build_dashboard_data(target_date)
    return render_template("dashboard.html", today=today, history=history, token=token)


@app.route("/0dte-daily/brief/")
@require_auth
def form():
    return send_from_directory(str(BASE_DIR), "daily_brief_form.html")


@app.route("/0dte-daily/premarket")
@require_auth
def premarket():
    """Return today's pre-market SPX price fetched at 6 AM ET."""
    today = str(date.today())
    path = BASE_DIR / config.MARKET_DATA_DIR / f"premarket_{today}.json"
    if not path.exists():
        return jsonify({"ok": False, "error": "No premarket data yet for today"}), 404
    try:
        data = json.loads(path.read_text())
        return jsonify({"ok": True, **data})
    except Exception:
        return jsonify({"ok": False, "error": "Could not read premarket file"}), 500



@app.route("/0dte-daily/submit", methods=["POST"])
@require_auth
def submit():
    try:
        brief = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400

    if not isinstance(brief, dict) or not brief:
        return jsonify({"ok": False, "error": "Brief must be a non-empty JSON object"}), 400

    err = validate_brief(brief)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    target_date = brief.get("date", str(date.today()))
    validate_date(target_date)

    brief_dir = BASE_DIR / config.DAILY_BRIEF_DIR
    brief_dir.mkdir(exist_ok=True)
    brief_path = brief_dir / f"{target_date}.json"
    brief_path.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    app.logger.info(f"Brief saved: {brief_path}")

    html_path, err = run_assembly(target_date)
    if err:
        app.logger.warning(f"Assembly warning for {target_date}: {err}")
        return jsonify({
            "ok": True,
            "assembled": False,
            "warning": "Assembly incomplete — market data may be pending.",
            "preview_url": f"/0dte-daily/preview/{target_date}",
        })

    notify_preview_ready(target_date, brief.get("signal_color", "yellow"))

    return jsonify({
        "ok": True,
        "assembled": True,
        "preview_url": f"/0dte-daily/preview/{target_date}",
    })


@app.route("/0dte-daily/preview/<target_date>")
@require_auth
def preview(target_date):
    validate_date(target_date)

    draft_path = DRAFTS_DIR / f"{target_date}.html"
    brief_path = BASE_DIR / config.DAILY_BRIEF_DIR / f"{target_date}.json"
    market_path = BASE_DIR / config.MARKET_DATA_DIR / f"{target_date}.json"

    brief_exists  = brief_path.exists()
    draft_exists  = draft_path.exists()
    market_exists = market_path.exists()

    brief = json.loads(brief_path.read_text()) if brief_exists else {}
    signal_color = brief.get("signal_color", "yellow")

    approved_path = DRAFTS_DIR / f"{target_date}.approved"
    is_approved = approved_path.exists()
    approved_data = json.loads(approved_path.read_text()) if is_approved else {}

    default_seg_ids = [
        int(s.strip())
        for s in config.OPTIPUB_DEFAULT_SEGMENTS.split(",")
        if s.strip()
    ]
    segments = [
        {"id": sid, "name": config.SEGMENT_NAMES.get(sid, f"Segment {sid}")}
        for sid in default_seg_ids
    ]

    return render_template(
        "preview.html",
        target_date=target_date,
        draft_exists=draft_exists,
        brief_exists=brief_exists,
        market_exists=market_exists,
        signal_color=signal_color,
        is_approved=is_approved,
        optipub_msg_id=approved_data.get("msg_id"),
        optipub_title=approved_data.get("title"),
        optipub_connected=bool(config.OPTIPUB_API_KEY),
        segments=segments,
    )


@app.route("/0dte-daily/draft/<target_date>")
@require_auth
def draft_html(target_date):
    validate_date(target_date)
    return send_from_directory(str(DRAFTS_DIR), f"{target_date}.html")


@app.route("/0dte-daily/approve/<target_date>", methods=["POST"])
@require_auth
def approve(target_date):
    validate_date(target_date)

    draft_path = DRAFTS_DIR / f"{target_date}.html"
    brief_path = BASE_DIR / config.DAILY_BRIEF_DIR / f"{target_date}.json"

    if not draft_path.exists():
        return jsonify({"ok": False, "error": "No draft found for this date."}), 404

    if not config.OPTIPUB_API_KEY:
        return jsonify({
            "ok": False,
            "error": "OPTIPUB_API_KEY not set. Add it to the server .env file."
        }), 503

    try:
        from assemble_newsletter import create_optipub_draft
        brief  = json.loads(brief_path.read_text()) if brief_path.exists() else {}
        html   = draft_path.read_text(encoding="utf-8")

        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "Request body must be a JSON object"}), 400

        default_seg_ids = {
            int(s.strip())
            for s in config.OPTIPUB_DEFAULT_SEGMENTS.split(",")
            if s.strip()
        }
        allowed_ids = set(config.SEGMENT_NAMES.keys()) | default_seg_ids
        included_raw = body.get("included_segments", [])
        excluded_raw = body.get("excluded_segments", [])
        try:
            included = [int(i) for i in included_raw]
            excluded = [int(i) for i in excluded_raw]
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Segment IDs must be integers"}), 400
        unknown = (set(included) | set(excluded)) - allowed_ids
        if unknown:
            return jsonify({"ok": False, "error": f"Unknown segment IDs: {sorted(unknown)}"}), 400

        msg_id, title = create_optipub_draft(
            html, brief, target_date,
            included_segments=included or None,
            excluded_segments=excluded or None,
        )
    except Exception:
        app.logger.exception("approve failed")
        notify_deliverability_issue(target_date, "OptiPub draft creation failed")
        return jsonify({"ok": False, "error": "Approve failed. Check server logs."}), 500

    approved_path = DRAFTS_DIR / f"{target_date}.approved"
    approved_path.write_text(json.dumps({"msg_id": msg_id, "title": title,
                                         "approved_at": datetime.utcnow().isoformat()}))

    notify_approved(target_date, msg_id, title)

    return jsonify({"ok": True, "msg_id": msg_id, "title": title})


@app.route("/0dte-daily/suggest-subject/<target_date>", methods=["GET"])
@require_auth
def suggest_subject(target_date):
    validate_date(target_date)

    brief_path = BASE_DIR / config.DAILY_BRIEF_DIR / f"{target_date}.json"
    brief = json.loads(brief_path.read_text()) if brief_path.exists() else {}

    signal_color = brief.get("signal_color", "yellow")
    signal_label = signal_config(signal_color)["label"]
    signal_text  = (brief.get("signal_text") or "").strip()
    editor_note  = (brief.get("editor_note_text") or "").strip()

    # Sensible defaults in case Claude is unavailable or brief is empty
    default_subject = f"0DTE Daily — {signal_label} signal"
    default_preview = f"Today's setup and what to watch at the open"

    if not config.ANTHROPIC_API_KEY:
        return jsonify({"subject": default_subject, "preview_line": default_preview})

    try:
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

        prompt_parts = [f"Signal: {signal_label}"]
        if signal_text:
            prompt_parts.append(f"Signal context: {signal_text}")
        if editor_note:
            prompt_parts.append(f"Editor's note: {editor_note}")

        brief_summary = "\n".join(prompt_parts)

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=(
                "You write compelling email subject lines and preview text for a "
                "daily options trading newsletter called '0DTE Daily'. "
                "The audience is active options traders. "
                "Keep subjects under 60 characters. Keep preview lines under 100 characters. "
                "Base both on the editor's note and the signal. "
                "Be specific, intriguing, and direct — create a sense of urgency or insight. "
                "Never use %, $, or em dashes (—). Use plain hyphens if you need a dash. "
                "Do not use hype words like 'explosive', 'massive', or 'huge'."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Write a subject line and preview line for today's newsletter.\n\n"
                    f"{brief_summary}\n\n"
                    "Respond with exactly two lines in this format:\n"
                    "Subject: <subject line>\n"
                    "Preview: <preview line>"
                ),
            }],
        )

        def sanitize(s):
            return s.replace("$", "").replace("%", "").replace("—", "-").replace("–", "-").strip()

        text = next((b.text for b in response.content if b.type == "text"), "")
        subject = default_subject
        preview = default_preview
        for line in text.splitlines():
            if line.startswith("Subject:"):
                val = sanitize(line[len("Subject:"):])
                if val:
                    subject = val
            elif line.startswith("Preview:"):
                val = sanitize(line[len("Preview:"):])
                if val:
                    preview = val

        return jsonify({"subject": subject, "preview_line": preview})

    except Exception:
        app.logger.exception("suggest-subject: Claude call failed, returning defaults")
        return jsonify({"subject": default_subject, "preview_line": default_preview})


@app.route("/0dte-daily/send-test/<target_date>", methods=["POST"])
@require_auth
def send_test(target_date):
    validate_date(target_date)

    draft_path = DRAFTS_DIR / f"{target_date}.html"
    if not draft_path.exists():
        return jsonify({"ok": False, "error": "No draft found for this date."}), 404

    if not config.OPTIPUB_API_KEY:
        return jsonify({
            "ok": False,
            "error": "OPTIPUB_API_KEY not set. Add it to the server .env file."
        }), 503

    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Request body must be a JSON object"}), 400
    email = data.get("email", "").strip()
    if not email:
        return jsonify({"ok": False, "error": "No email address provided."}), 400
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify({"ok": False, "error": "Invalid email format."}), 400

    # Accept caller-supplied subject and preview_line; fall back to signal-based defaults
    brief_path = BASE_DIR / config.DAILY_BRIEF_DIR / f"{target_date}.json"
    brief = json.loads(brief_path.read_text()) if brief_path.exists() else {}
    signal_label = signal_config(brief.get("signal_color", "yellow"))["label"]

    raw_subject = data.get("subject", "").strip()[:200]
    raw_preview = data.get("preview_line", "").strip()[:200]
    # Strip CR/LF to prevent header injection
    raw_subject = raw_subject.replace("\r", "").replace("\n", "")
    raw_preview = raw_preview.replace("\r", "").replace("\n", "")

    subject = raw_subject or f"0DTE Daily — {signal_label} — {target_date}"
    preview_line = raw_preview or f"{signal_label} — {target_date}"

    try:
        html       = draft_path.read_text(encoding="utf-8")

        payload = json.dumps({
            "email":        email,
            "subject":      subject,
            "html":         html,
            "sender_id":    config.OPTIPUB_SENDER_ID,
            "reply_id":     config.OPTIPUB_SENDER_ID,
            "preview_line": preview_line,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{config.OPTIPUB_API_BASE}/messages/transactionals",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept":        "application/json",
                "Authorization": f"Bearer {config.OPTIPUB_API_KEY}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                app.logger.warning(f"send-test HTTP {resp.status}: {body[:300]}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            app.logger.error(f"send-test HTTP {e.code}: {body}")
            notify_deliverability_issue(target_date, f"Test send to {email} failed — HTTP {e.code}: {body}")
            return jsonify({"ok": False, "error": f"OptiPub error {e.code}: {body}"}), 500

        return jsonify({"ok": True, "email": email})
    except Exception:
        app.logger.exception("send-test failed")
        notify_deliverability_issue(target_date, f"Test send to {email} failed")
        return jsonify({"ok": False, "error": "Send failed. Check server logs."}), 500


@app.route("/0dte-daily/sync", methods=["POST"])
@require_auth
def sync_market_data():
    import zoneinfo
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "Request body must be a JSON object"}), 400
    mode = body.get("mode", "quotes")
    if mode not in ("options", "quotes", "full"):
        return jsonify({"ok": False, "error": "Invalid mode"}), 400

    et_date = datetime.now(zoneinfo.ZoneInfo("America/New_York")).date().isoformat()
    try:
        result = subprocess.run(
            ["python3", str(SCRIPTS_DIR / "fetch_market_data.py"),
             "--mode", mode, "--date", et_date, "--force"],
            cwd=str(BASE_DIR),
            capture_output=True, text=True,
            timeout=60,
            env=_subprocess_env()
        )
    except subprocess.TimeoutExpired:
        notify_fetch_failed(f"manual sync ({mode})", "Timed out after 60s")
        return jsonify({"ok": False, "error": "Fetch timed out"}), 500

    if result.returncode != 0:
        error = _subprocess_error(result)
        notify_fetch_failed(f"manual sync ({mode})", error)
        app.logger.error(f"Manual sync ({mode}) failed:\n{error}")
        return jsonify({"ok": False, "error": error[:800]}), 500

    app.logger.info(f"Manual sync ({mode}) succeeded")
    return jsonify({"ok": True, "output": result.stdout})


@app.route("/0dte-daily/rerender/<target_date>", methods=["POST"])
@require_auth
def rerender(target_date):
    validate_date(target_date)
    html_path, err = run_assembly(target_date)
    if err:
        app.logger.warning("rerender failed: %s", err)
        return jsonify({"ok": False, "error": "Re-render failed. Check server logs."}), 500
    return jsonify({"ok": True, "message": f"Re-rendered for {target_date}"})


@app.route("/0dte-daily/brief-data/<target_date>")
@require_auth
def brief_data(target_date):
    validate_date(target_date)
    brief_path = BASE_DIR / config.DAILY_BRIEF_DIR / f"{target_date}.json"
    if not brief_path.exists():
        return jsonify({"ok": False, "error": "No brief found"}), 404
    try:
        return jsonify(json.loads(brief_path.read_text()))
    except Exception:
        return jsonify({"ok": False, "error": "Could not read brief"}), 500


@app.route("/0dte-daily/upload-chart/<target_date>", methods=["POST"])
@require_auth
def upload_chart(target_date):
    validate_date(target_date)

    if "chart" not in request.files:
        return jsonify({"ok": False, "error": "No file provided"}), 400
    file = request.files["chart"]
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "No file selected"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
        return jsonify({"ok": False, "error": "Only JPG, PNG, GIF, or WebP images are allowed"}), 400

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > 5 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Image must be under 5 MB"}), 400

    filename = f"levels_{target_date}.{ext}"
    file.save(str(CHARTS_DIR / filename))

    url = f"{config.SERVER_BASE_URL}/0dte-daily/charts/{filename}"
    app.logger.info(f"Chart uploaded for {target_date}: {filename}")
    return jsonify({"ok": True, "url": url})


@app.route("/0dte-daily/charts/<filename>")
def serve_chart(filename):
    """Serve chart images without auth — email clients must be able to fetch them."""
    if not re.match(r'^levels_\d{4}-\d{2}-\d{2}\.(jpg|jpeg|png|gif|webp)$', filename):
        abort(404)
    return send_from_directory(str(CHARTS_DIR), filename)


@app.route("/0dte-daily/status")
@require_auth
def status():
    today = str(date.today())
    return jsonify({
        "date": today,
        "brief":       (BASE_DIR / config.DAILY_BRIEF_DIR / f"{today}.json").exists(),
        "market_data": (BASE_DIR / config.MARKET_DATA_DIR / f"{today}.json").exists(),
        "draft":       (DRAFTS_DIR / f"{today}.html").exists(),
        "approved":    (DRAFTS_DIR / f"{today}.approved").exists(),
        "polygon_key": bool(config.POLYGON_API_KEY),
    })


# ── Scheduled jobs ────────────────────────────────────────────────────────────

def job_morning_check():
    today_date = date.today()
    today_str = str(today_date)

    if not is_trading_day():
        app.logger.info("Morning check: not a trading day, skipping.")
        return

    missing = []

    if not (BASE_DIR / config.DAILY_BRIEF_DIR / f"{today_str}.json").exists():
        missing.append("Brief not submitted")

    market_data_date = str(market_data_date_for_newsletter(today_date))
    market_path = BASE_DIR / config.MARKET_DATA_DIR / f"{market_data_date}.json"
    market_ok = False
    if market_path.exists():
        try:
            md = json.loads(market_path.read_text())
            if md.get("spx", {}).get("close"):
                market_ok = True
        except Exception:
            pass
    if not market_ok:
        missing.append("Market data missing or incomplete")

    if not (DRAFTS_DIR / f"{today_str}.html").exists():
        missing.append("Newsletter draft not assembled")

    if not missing:
        app.logger.info(f"Morning check ({today_str}): all pipeline items complete — no notification needed.")
        return

    if not config.SLACK_WEBHOOK_URL:
        app.logger.warning("Morning check: SLACK_WEBHOOK_URL not set, cannot notify.")
        return

    bullets = "\n".join(f"• {item}" for item in missing)
    dashboard_url = config.SERVER_BASE_URL + "/0dte-daily/"
    _send_slack(
        f":alarm_clock: *0DTE Daily — not ready for send* ({today_str})\n"
        f"It's 6:15 AM PT and the following are incomplete:\n"
        f"{bullets}\n"
        f"<{dashboard_url}|Go to dashboard>"
    )
    app.logger.info(f"Morning check: notified Slack — {len(missing)} item(s) incomplete.")


def job_fetch_options():
    if not is_trading_day():
        app.logger.info("Skipping options fetch — not a trading day.")
        return
    app.logger.info("Scheduled: fetch options chain (phase 1)...")
    result = subprocess.run(
        ["python3", str(SCRIPTS_DIR / "fetch_market_data.py"), "--mode", "options"],
        cwd=str(BASE_DIR),
        capture_output=True, text=True,
        timeout=120, env=_subprocess_env()
    )
    if result.returncode != 0:
        error = _subprocess_error(result)
        app.logger.error(f"options fetch failed:\n{error}")
        notify_fetch_failed("options (3:50 PM ET)", error)
    else:
        app.logger.info(f"options fetch done:\n{result.stdout}")


def job_fetch_quotes():
    if not is_trading_day():
        app.logger.info("Skipping quotes fetch — not a trading day.")
        return
    app.logger.info("Scheduled: fetch closing quotes (phase 2)...")
    result = subprocess.run(
        ["python3", str(SCRIPTS_DIR / "fetch_market_data.py"), "--mode", "quotes"],
        cwd=str(BASE_DIR),
        capture_output=True, text=True,
        timeout=60, env=_subprocess_env()
    )
    if result.returncode != 0:
        error = _subprocess_error(result)
        app.logger.error(f"quotes fetch failed:\n{error}")
        notify_fetch_failed("quotes (4:35 PM ET)", error)
        return

    app.logger.info(f"quotes fetch done:\n{result.stdout}")

    today = str(date.today())
    draft_path    = DRAFTS_DIR / f"{today}.html"
    approved_path = DRAFTS_DIR / f"{today}.approved"
    brief_path    = BASE_DIR / config.DAILY_BRIEF_DIR / f"{today}.json"

    if draft_path.exists() and not approved_path.exists() and brief_path.exists():
        app.logger.info(f"Auto re-rendering draft for {today} with fresh market data...")
        html_path, err = run_assembly(today)
        if err:
            app.logger.warning(f"Auto re-render failed: {err}")
        else:
            app.logger.info(f"Draft re-rendered: {html_path}")
            notify_preview_ready(
                today,
                json.loads(brief_path.read_text()).get("signal_color", "yellow")
            )


def job_auth_health():
    app.logger.info("Scheduled: auth_health check...")
    result = subprocess.run(
        ["python3", str(SCRIPTS_DIR / "auth_health.py")],
        cwd=str(BASE_DIR),
        capture_output=True, text=True,
        timeout=30, env=_subprocess_env()
    )
    app.logger.info(f"auth_health output:\n{result.stdout}")
    if result.returncode != 0:
        app.logger.warning(f"auth_health warning:\n{_subprocess_error(result)}")


def job_fetch_premarket():
    if not is_trading_day():
        app.logger.info("Skipping premarket fetch — not a trading day.")
        return
    app.logger.info("Scheduled: fetch SPX pre-market price (6:00 AM ET)...")
    result = subprocess.run(
        ["python3", str(SCRIPTS_DIR / "fetch_premarket.py")],
        cwd=str(BASE_DIR),
        capture_output=True, text=True,
        timeout=30, env=_subprocess_env()
    )
    if result.returncode != 0:
        error = _subprocess_error(result)
        app.logger.error(f"premarket fetch failed:\n{error}")
        notify_fetch_failed("premarket (6:00 AM ET)", error)
    else:
        app.logger.info(f"premarket fetch done:\n{result.stdout}")


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="America/New_York")
    scheduler.add_job(job_fetch_premarket, "cron",
                      day_of_week="mon-fri", hour=6, minute=0,
                      id="fetch_premarket")
    scheduler.add_job(job_fetch_options, "cron",
                      day_of_week="mon-fri", hour=15, minute=50,
                      id="fetch_options")
    scheduler.add_job(job_fetch_quotes, "cron",
                      day_of_week="mon-fri", hour=16, minute=35,
                      id="fetch_quotes")
    scheduler.add_job(job_auth_health, "cron",
                      day_of_week="mon-fri", hour=9, minute=0,
                      id="auth_health")
    scheduler.add_job(job_morning_check, "cron",
                      day_of_week="mon-fri", hour=9, minute=15,
                      id="morning_check")
    scheduler.start()
    app.logger.info("Scheduler started (premarket 6:00, options 3:50, quotes 4:35, auth 9:00, morning_check 9:15 — all ET)")
    return scheduler


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    scheduler = start_scheduler()
    app.run(host="0.0.0.0", port=8001, debug=False)
