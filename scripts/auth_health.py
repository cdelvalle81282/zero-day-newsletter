"""
Zero Day Newsletter — Auth Health Checker
Verifies the Polygon.io API key is set and reachable.
Run daily via the scheduler (9:00 AM ET).
"""

import sys
import json
import urllib.request

import config


def check_polygon():
    if not config.POLYGON_API_KEY:
        print("ERROR: POLYGON_API_KEY is not set in .env")
        return False

    url = (
        f"https://api.polygon.io/v3/snapshot"
        f"?ticker.any_of=I:SPX&apiKey={config.POLYGON_API_KEY}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            if data.get("status") in ("OK", "DELAYED"):
                print(f"Polygon API healthy — status: {data.get('status')}")
                return True
            else:
                print(f"Polygon API unexpected response: {data.get('status')}")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        print(f"ERROR: Polygon API returned {e.code} — {body}")
        return False
    except Exception as e:
        print(f"ERROR: Could not reach Polygon API — {e}")
        return False


def notify_failure(detail):
    webhook = config.SLACK_WEBHOOK_URL
    if not webhook:
        return
    payload = json.dumps({
        "text": f":warning: *0DTE Daily — Polygon API issue*\n{detail}"
    }).encode()
    try:
        req = urllib.request.Request(
            webhook, data=payload,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def main():
    ok = check_polygon()
    if not ok:
        notify_failure("Polygon API key may be invalid or quota exceeded. Check .env.")
        sys.exit(1)


if __name__ == "__main__":
    main()
