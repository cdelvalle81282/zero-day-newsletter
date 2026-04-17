"""
Zero Day Newsletter — Auth Health Checker
Checks when the Schwab refresh token expires and sends a reminder
if it's within the warning window.

Run daily via cron:
    0 9 * * 1-5  python3 /path/to/scripts/auth_health.py
"""

import json
import os
import smtplib
import sys
import urllib.request
from datetime import datetime, timezone
from email.mime.text import MIMEText

import config


def load_token():
    if not os.path.exists(config.TOKEN_FILE):
        return None
    with open(config.TOKEN_FILE) as f:
        return json.load(f)


def get_expiry(token_data):
    """
    Schwab refresh tokens expire 7 days from when the token was last created/refreshed.
    schwab-py stores 'creation_timestamp' as a Unix timestamp.
    """
    creation = token_data.get("creation_timestamp")
    if not creation:
        return None
    expiry_ts = creation + (config.SCHWAB_REFRESH_TOKEN_DAYS * 86400)
    return datetime.fromtimestamp(expiry_ts, tz=timezone.utc)


def days_until_expiry(expiry_dt):
    now = datetime.now(tz=timezone.utc)
    delta = expiry_dt - now
    return delta.days + (1 if delta.seconds > 0 else 0)


# ── Notifications ─────────────────────────────────────────────────────────────

def send_email(days_remaining, expiry_dt):
    expiry_str = expiry_dt.strftime("%A, %B %-d at %-I:%M %p UTC")
    urgency = "⚠️ ACTION NEEDED" if days_remaining <= 1 else "Heads up"

    body = f"""{urgency}: Your Schwab API refresh token expires in {days_remaining} day{'s' if days_remaining != 1 else ''}.

Expiry: {expiry_str}

To renew, run this command on your machine:
    python3 scripts/reauth.py

This opens a browser window for a quick Schwab login. Takes ~60 seconds.
The fetch script will fail silently if you miss this window.

— Zero Day Automation
"""
    msg = MIMEText(body)
    msg["Subject"] = f"[Zero Day] Schwab token expires in {days_remaining}d — run reauth.py"
    msg["From"]    = config.NOTIFY_EMAIL_FROM
    msg["To"]      = config.NOTIFY_EMAIL_TO

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(config.NOTIFY_EMAIL_FROM, config.GMAIL_APP_PASSWORD)
        server.send_message(msg)

    print(f"Email reminder sent to {config.NOTIFY_EMAIL_TO}")


def send_slack(days_remaining, expiry_dt):
    expiry_str = expiry_dt.strftime("%A %b %-d at %-I:%M %p UTC")
    urgency = ":rotating_light: *ACTION NEEDED*" if days_remaining <= 1 else ":warning:"

    text = (
        f"{urgency} Schwab API refresh token expires in *{days_remaining} day{'s' if days_remaining != 1 else ''}* "
        f"({expiry_str}).\n"
        f"Run `python3 scripts/reauth.py` to renew. Takes ~60 seconds."
    )
    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        config.SLACK_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req)
    print("Slack reminder sent.")


def notify(days_remaining, expiry_dt):
    try:
        if config.NOTIFY_METHOD == "slack":
            send_slack(days_remaining, expiry_dt)
        else:
            send_email(days_remaining, expiry_dt)
    except Exception as e:
        print(f"WARNING: Could not send notification — {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    token_data = load_token()

    if token_data is None:
        print("ERROR: No token file found. Run reauth.py to set up auth.")
        notify(0, datetime.now(tz=timezone.utc))
        sys.exit(1)

    expiry_dt = get_expiry(token_data)
    if expiry_dt is None:
        print("ERROR: Could not read token creation timestamp.")
        sys.exit(1)

    days = days_until_expiry(expiry_dt)
    expiry_str = expiry_dt.strftime("%Y-%m-%d %H:%M UTC")

    print(f"Token expires: {expiry_str}")
    print(f"Days remaining: {days}")

    if days <= 0:
        print("EXPIRED — fetch_market_data.py will fail. Run reauth.py immediately.")
        notify(0, expiry_dt)
        sys.exit(1)
    elif days <= config.REMINDER_DAYS_BEFORE_EXPIRY:
        print(f"Within reminder window ({config.REMINDER_DAYS_BEFORE_EXPIRY}d). Sending notification...")
        notify(days, expiry_dt)
    else:
        print(f"Token healthy. Next reminder in {days - config.REMINDER_DAYS_BEFORE_EXPIRY} day(s).")


if __name__ == "__main__":
    main()
