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
    "levels_note",
    "editor_note_text",
}

VALID_SIGNAL_COLORS = {"green", "yellow", "red"}
MAX_TEXT_LEN = 5000


# ── Security helpers ──────────────────────────────────────────────────────────

def validate_date(d):
    """Reject any target_date that isn't YYYY-MM-DD format."""
    if not DATE_RE.fullmatch(d):
        abort(400, "Invalid date format")


def validate_brief(brief):
    """Validate brief JSON keys, types, and values. Returns error string or None."""
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
    if d and not DATE_RE.fullmatch(d):
        return "Invalid date format in brief"

    for key, val in brief.items():
        if key.endswith("_value"):
            if val is not None and not isinstance(val, (int, float)):
                return f"{key} must be numeric or null"
        elif key.endswith(("_text", "_label", "_note", "_attribution")):
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

def notify_preview_ready(target_date, signal_color="yellow"):
    webhook = config.SLACK_WEBHOOK_URL
    if not webhook:
        return

    preview_url = f"{config.SERVER_BASE_URL}/0dte-daily/preview/{target_date}"
    color_emoji = {"green": ":large_green_circle:", "red": ":red_circle:"}.get(
        signal_color, ":large_yellow_circle:"
    )

    import urllib.request
    payload = json.dumps({
        "text": (
            f"{color_emoji} *0DTE Daily draft is ready for review*\n"
            f"Date: {target_date}\n"
            f"<{preview_url}|Click here to preview and approve>"
        )
    }).encode()

    try:
        req = urllib.request.Request(
            webhook, data=payload,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        app.logger.warning(f"Slack notification failed: {e}")


def notify_approved(target_date, msg_id, title):
    webhook = config.SLACK_WEBHOOK_URL
    if not webhook:
        return
    import urllib.request
    payload = json.dumps({
        "text": f":white_check_mark: *0DTE Daily approved and posted to OptiPub*\n"
                f"Date: {target_date} | Message ID: {msg_id}\nTitle: {title}"
    }).encode()
    try:
        req = urllib.request.Request(webhook, data=payload,
                                      headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


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
    if prev_market_path.exists():
        market = json.loads(prev_market_path.read_text())
        market_file_date = prev_trading_day
    elif market_exists:
        market = json.loads(market_path.read_text())
        market_file_date = target_date
    else:
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
        "key_label": brief.get("level_key_label", "Key Level"),
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
            f"${tn['open']:.2f}, hit ${tn['high']:.2f} — "
            f"${int(tn['gain_dollars']):,} per contract."
        )

    token_path = Path(config.TOKEN_FILE)
    token_data = {}
    if token_path.exists():
        try:
            token_data = json.loads(token_path.read_text())
        except Exception:
            pass
    creation = token_data.get("creation_timestamp")
    if creation:
        from datetime import timezone
        expiry_ts = creation + (config.SCHWAB_REFRESH_TOKEN_DAYS * 86400)
        expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
        days_left = max(0, (expiry_dt - datetime.now(timezone.utc)).days)
        token_info = {
            "exists":    True,
            "days_left": days_left,
            "expires":   expiry_dt.strftime("%b %-d") if os.name != "nt" else expiry_dt.strftime("%b {d}").replace("{d}", str(expiry_dt.day)),
        }
    else:
        token_info = {"exists": token_path.exists(), "days_left": 0, "expires": "unknown"}

    today_data = {
        "date":          target_date,
        "brief":         brief_exists,
        "market_data":   market_exists,
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


@app.route("/0dte-daily/submit", methods=["POST"])
@require_auth
def submit():
    try:
        brief = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400

    if not brief:
        return jsonify({"ok": False, "error": "Empty body"}), 400

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
        included = [int(i) for i in body.get("included_segments", [])]
        excluded = [int(i) for i in body.get("excluded_segments", [])]

        msg_id, title = create_optipub_draft(
            html, brief, target_date,
            included_segments=included or None,
            excluded_segments=excluded or None,
        )
    except Exception:
        app.logger.exception("approve failed")
        return jsonify({"ok": False, "error": "Approve failed. Check server logs."}), 500

    approved_path = DRAFTS_DIR / f"{target_date}.approved"
    approved_path.write_text(json.dumps({"msg_id": msg_id, "title": title,
                                         "approved_at": datetime.utcnow().isoformat()}))

    notify_approved(target_date, msg_id, title)

    return jsonify({"ok": True, "msg_id": msg_id, "title": title})


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

    data = request.get_json(force=True) or {}
    email = data.get("email", "").strip()
    if not email:
        return jsonify({"ok": False, "error": "No email address provided."}), 400
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify({"ok": False, "error": "Invalid email format."}), 400

    try:
        import urllib.request as _urlreq
        html      = draft_path.read_text(encoding="utf-8")
        brief_path = BASE_DIR / config.DAILY_BRIEF_DIR / f"{target_date}.json"
        brief     = json.loads(brief_path.read_text()) if brief_path.exists() else {}

        signal_label = signal_config(brief.get("signal_color", "yellow"))["label"]
        subject = f"[TEST] 0DTE Daily — {signal_label} — {target_date}"

        payload = json.dumps({
            "email":          email,
            "subject":        subject,
            "content":        html,
            "sender_id":      config.OPTIPUB_SENDER_ID,
            "publication_id": config.ZERO_DAY_PUBLICATION_ID,
            "preview_line":   f"Test send — {signal_label} — {target_date}",
        }).encode("utf-8")

        req = _urlreq.Request(
            f"{config.OPTIPUB_API_BASE}/messages/transactional/html",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.OPTIPUB_API_KEY}",
            },
            method="POST",
        )
        with _urlreq.urlopen(req, timeout=15) as resp:
            json.loads(resp.read())

        return jsonify({"ok": True, "email": email})
    except Exception:
        app.logger.exception("send-test failed")
        return jsonify({"ok": False, "error": "Send failed. Check server logs."}), 500


@app.route("/0dte-daily/rerender/<target_date>", methods=["POST"])
@require_auth
def rerender(target_date):
    validate_date(target_date)
    html_path, err = run_assembly(target_date)
    if err:
        app.logger.warning("rerender failed: %s", err)
        return jsonify({"ok": False, "error": "Re-render failed. Check server logs."}), 500
    return jsonify({"ok": True, "message": f"Re-rendered for {target_date}"})


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
        "token_file":  Path(config.TOKEN_FILE).exists(),
    })


# ── Scheduled jobs ────────────────────────────────────────────────────────────

def job_fetch_options():
    if not is_trading_day():
        app.logger.info("Skipping options fetch — not a trading day.")
        return
    app.logger.info("Scheduled: fetch options chain (phase 1)...")
    result = subprocess.run(
        ["python3", str(SCRIPTS_DIR / "fetch_market_data.py"), "--mode", "options"],
        cwd=str(BASE_DIR),
        capture_output=True, text=True
    )
    if result.returncode != 0:
        app.logger.error(f"options fetch failed:\n{result.stderr}")
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
        capture_output=True, text=True
    )
    if result.returncode != 0:
        app.logger.error(f"quotes fetch failed:\n{result.stderr}")
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
        capture_output=True, text=True
    )
    app.logger.info(f"auth_health output:\n{result.stdout}")
    if result.returncode != 0:
        app.logger.warning(f"auth_health warning:\n{result.stderr}")


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="America/New_York")
    scheduler.add_job(job_fetch_options, "cron",
                      day_of_week="mon-fri", hour=15, minute=50,
                      id="fetch_options")
    scheduler.add_job(job_fetch_quotes, "cron",
                      day_of_week="mon-fri", hour=16, minute=35,
                      id="fetch_quotes")
    scheduler.add_job(job_auth_health, "cron",
                      day_of_week="mon-fri", hour=9, minute=0,
                      id="auth_health")
    scheduler.start()
    app.logger.info("Scheduler started (options 3:50, quotes 4:35, auth 9:00 — all ET)")
    return scheduler


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    scheduler = start_scheduler()
    app.run(host="0.0.0.0", port=8001, debug=False)
