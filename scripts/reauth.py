"""
Zero Day Newsletter — Schwab Re-Authentication
Run this whenever your refresh token expires (every ~7 days) or
when auth_health.py tells you to.

Usage:
    python3 scripts/reauth.py

Opens a browser window for Schwab login. After you approve,
the token is saved and all other scripts work again.
"""

import os
import sys

try:
    from schwab import auth
except ImportError:
    print("ERROR: schwab-py not installed. Run: pip install schwab-py")
    sys.exit(1)

import config


def main():
    print("=" * 55)
    print("  Zero Day — Schwab Re-Authentication")
    print("=" * 55)
    print()
    print("This will open a browser window for Schwab login.")
    print("After you log in and approve, the token is saved")
    print(f"to: {config.TOKEN_FILE}")
    print()

    # Back up old token if it exists
    if os.path.exists(config.TOKEN_FILE):
        backup = config.TOKEN_FILE + ".bak"
        os.replace(config.TOKEN_FILE, backup)
        print(f"Old token backed up to: {backup}")

    print("A browser window will open. Log into Schwab, approve access,")
    print("then copy the FULL URL from your browser address bar and")
    print("paste it back here when prompted.")
    print()

    try:
        c = auth.client_from_manual_flow(
            api_key=config.SCHWAB_APP_KEY,
            app_secret=config.SCHWAB_APP_SECRET,
            callback_url=config.SCHWAB_CALLBACK_URL,
            token_path=config.TOKEN_FILE,
        )
        import stat
        os.chmod(config.TOKEN_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        print()
        print("✓ Authentication successful.")
        print(f"✓ Token saved to {config.TOKEN_FILE} (permissions: owner-only)")
        print()
        print("You're good for the next 7 days.")
        print("auth_health.py will remind you before it expires again.")

    except Exception as e:
        print(f"\nERROR during authentication: {e}")
        # Restore backup if something went wrong
        backup = config.TOKEN_FILE + ".bak"
        if os.path.exists(backup) and not os.path.exists(config.TOKEN_FILE):
            os.replace(backup, config.TOKEN_FILE)
            print("Restored previous token from backup.")
        sys.exit(1)


if __name__ == "__main__":
    main()
