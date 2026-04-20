"""
Zero Day / 0DTE Daily Newsletter — Configuration

Local dev: edit values directly below.
Server: set environment variables — they take precedence over everything here.
"""

import os

def _env(key, default):
    return os.environ.get(key, default)

# ── Schwab API credentials ────────────────────────────────────────────────────
SCHWAB_APP_KEY      = _env("SCHWAB_APP_KEY",    "YOUR_APP_KEY")
SCHWAB_APP_SECRET   = _env("SCHWAB_APP_SECRET", "YOUR_APP_SECRET")
SCHWAB_CALLBACK_URL = _env("SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182")

# ── File paths ────────────────────────────────────────────────────────────────
TOKEN_FILE      = _env("TOKEN_FILE",      "schwab_token.json")
MARKET_DATA_DIR = _env("MARKET_DATA_DIR", "market_data")
DAILY_BRIEF_DIR = _env("DAILY_BRIEF_DIR", "daily_briefs")

# ── OptiPub ───────────────────────────────────────────────────────────────────
OPTIPUB_API_BASE        = _env("OPTIPUB_API_BASE", "https://optionpit.app.optipub.com/api/3.2")
OPTIPUB_API_KEY         = _env("OPTIPUB_API_KEY",  "")
ZERO_DAY_PUBLICATION_ID = int(_env("ZERO_DAY_PUBLICATION_ID", "103"))

# ── Auth health reminders ─────────────────────────────────────────────────────
REMINDER_DAYS_BEFORE_EXPIRY = int(_env("REMINDER_DAYS_BEFORE_EXPIRY", "3"))
SCHWAB_REFRESH_TOKEN_DAYS   = 7

# ── Notifications ─────────────────────────────────────────────────────────────
NOTIFY_METHOD        = _env("NOTIFY_METHOD",    "slack")
SLACK_WEBHOOK_URL    = _env("SLACK_WEBHOOK_URL", "")

# Email fallback (if NOTIFY_METHOD = "email")
NOTIFY_EMAIL_TO      = _env("NOTIFY_EMAIL_TO",   "cdelvalle@optionpit.com")
NOTIFY_EMAIL_FROM    = _env("NOTIFY_EMAIL_FROM",  "cdelvalle@optionpit.com")
GMAIL_APP_PASSWORD   = _env("GMAIL_APP_PASSWORD", "")

# ── Authors ───────────────────────────────────────────────────────────────────
AUTHORS = {
    "licia": {
        "name":  "Licia Leslie",
        "title": "0DTE Analyst, Option Pit",
        "photo": "https://optionpit.com/wp-content/uploads/2025/12/Avatar-Licia-Leslie.png",
    },
    "mark": {
        "name":  "Mark Sebastian",
        "title": "Chief Options Strategist, Option Pit",
        "photo": "https://optionpit.com/wp-content/uploads/2025/12/Avatar-Mark-Sebastian.png",
    },
    "olivia": {
        "name":  "Olivia Voz",
        "title": "Options Analyst, Option Pit",
        "photo": "https://optionpit.com/wp-content/uploads/2023/07/VOZ-HEADSHOT-FINAL.png",
    },
}
DEFAULT_AUTHOR = "licia"

# ── Segment display names (shown in the approve picker) ──────────────────────
SEGMENT_NAMES = {
    11:  "Staff List",
    338: "PDTE - 0DTE - Paid",
    339: "PDTE Paid List (static)",
    743: "VDTE - 0DTE - VIP",
    814: "F0DTE",
}

# ── OptiPub send settings ─────────────────────────────────────────────────────
# Sender ID to use for test sends and drafts.
# id:46 = "0DTE" sender on pub 41 — use until pub 103 gets its own sender.
OPTIPUB_SENDER_ID = int(_env("OPTIPUB_SENDER_ID", "46"))

# Default segments shown in the send picker (comma-separated IDs)
# 814 = F0DTE, 11 = Staff List
OPTIPUB_DEFAULT_SEGMENTS = _env("OPTIPUB_DEFAULT_SEGMENTS", "814,11")

# ── Anthropic ─────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY", "")

# ── Server ────────────────────────────────────────────────────────────────────
ZERODAY_PASSWORD = _env("ZERODAY_PASSWORD", "")   # basic auth for the form
SERVER_BASE_URL  = _env("SERVER_BASE_URL",  "https://optionpit-api.duckdns.org")
