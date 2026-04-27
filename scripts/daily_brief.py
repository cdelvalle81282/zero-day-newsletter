"""
Zero Day Newsletter — Daily Brief Input
Licia runs this each morning to enter the day's signal, levels, and notes.
Takes about 5 minutes. Saves a JSON file that the assembly engine reads.

Usage:
    python3 scripts/daily_brief.py
    python3 scripts/daily_brief.py --date 2026-04-17   (override date)
    python3 scripts/daily_brief.py --edit               (edit today's existing brief)
"""

import argparse
import json
import os
import sys
from datetime import date, datetime

import config


def ask(prompt, default=None, required=True):
    """Prompt for input with an optional default."""
    suffix = f" [{default}]" if default is not None else ""
    while True:
        val = input(f"{prompt}{suffix}: ").strip()
        if not val and default is not None:
            return default
        if not val and required:
            print("  (required)")
            continue
        return val or default


def ask_float(prompt, default=None):
    """Prompt for a decimal number."""
    while True:
        raw = ask(prompt, default=str(default) if default else None)
        try:
            return float(str(raw).replace(",", ""))
        except (ValueError, TypeError):
            print("  Please enter a number (e.g. 5780 or 5780.50)")


def ask_choice(prompt, choices):
    """Prompt for one of a fixed set of choices."""
    opts = "/".join(choices)
    while True:
        val = ask(f"{prompt} ({opts})").lower()
        if val in [c.lower() for c in choices]:
            return val
        print(f"  Please enter one of: {opts}")


def load_yesterday(target_date):
    """Load yesterday's brief to pre-fill level labels."""
    dt = datetime.strptime(str(target_date), "%Y-%m-%d")
    files = sorted([
        f for f in os.listdir(config.DAILY_BRIEF_DIR)
        if f.endswith(".json") and f < f"{target_date}.json"
    ]) if os.path.exists(config.DAILY_BRIEF_DIR) else []
    if not files:
        return {}
    with open(os.path.join(config.DAILY_BRIEF_DIR, files[-1])) as f:
        return json.load(f)


def collect(target_date, existing=None):
    """Interactive prompt to collect all Daily Brief fields."""
    prev = load_yesterday(target_date)
    ex   = existing or {}

    print()
    print("=" * 55)
    print("  Zero Day — Daily Brief")
    print(f"  {target_date}")
    print("=" * 55)
    print("  Press Enter to keep the value shown in [brackets].")
    print()

    # ── Signal ────────────────────────────────────────────────────────────────
    print("--- TODAY'S SIGNAL ---")
    signal_color = ask_choice("Signal color", ["green", "yellow", "red"])
    signal_text  = ask("Signal rationale (2-4 sentences)",
                       default=ex.get("signal_text"))

    # ── SPX Levels ────────────────────────────────────────────────────────────
    print()
    print("--- SPX LEVELS ---")
    print("  (Labels pre-filled from yesterday — update if changed)")

    r2_label = ask("Resistance 2 label",
                   default=ex.get("level_resistance_2_label")
                           or prev.get("level_resistance_2_label", "Resistance 2 (50-day MA)"))
    r2_value = ask_float("Resistance 2 value",
                         default=ex.get("level_resistance_2_value"))

    r1_label = ask("Resistance 1 label",
                   default=ex.get("level_resistance_1_label")
                           or prev.get("level_resistance_1_label", "Resistance 1"))
    r1_value = ask_float("Resistance 1 value",
                         default=ex.get("level_resistance_1_value"))

    key_label = ask("Premarket Price label",
                    default=ex.get("level_key_label")
                            or prev.get("level_key_label", "Premarket Price"))
    key_value = ask_float("Premarket Price value",
                          default=ex.get("level_key_value"))

    s1_label = ask("Support 1 label",
                   default=ex.get("level_support_1_label")
                           or prev.get("level_support_1_label", "Support 1"))
    s1_value = ask_float("Support 1 value",
                         default=ex.get("level_support_1_value"))

    s2_label = ask("Support 2 label",
                   default=ex.get("level_support_2_label")
                           or prev.get("level_support_2_label", "Support 2"))
    s2_value = ask_float("Support 2 value",
                         default=ex.get("level_support_2_value"))

    levels_note = ask("Levels note (1-2 sentences, optional)",
                      default=ex.get("levels_note", ""),
                      required=False)

    # ── The Number ────────────────────────────────────────────────────────────
    print()
    print("--- THE NUMBER ---")
    the_number_value = ask("The Number (e.g. +4,700%)",
                           default=ex.get("the_number_value"))
    the_number_text  = ask("Explanation paragraph",
                           default=ex.get("the_number_text"))

    # ── Volume Anomaly ────────────────────────────────────────────────────────
    print()
    print("--- VOLUME ANOMALY ---")
    volume_headline = ask("Headline (e.g. 'SPX 0DTE Volume: 2.8M Contracts')",
                          default=ex.get("volume_anomaly_headline"))
    volume_text     = ask("Narrative paragraph",
                          default=ex.get("volume_anomaly_text"))

    # ── CTA Block ─────────────────────────────────────────────────────────────
    print()
    print("--- CTA BLOCK (newsletter/product push) ---")
    print("  (Pre-fills from yesterday — only update when changing the offer)")
    cta_headline    = ask("CTA headline",
                          default=ex.get("cta_headline")    or prev.get("cta_headline",    "Trade 0DTE With the Pros"),
                          required=False) or ""
    cta_body        = ask("CTA body text",
                          default=ex.get("cta_body")        or prev.get("cta_body",        ""),
                          required=False) or ""
    cta_url         = ask("CTA link URL",
                          default=ex.get("cta_url")         or prev.get("cta_url",         "https://optionpit.com"),
                          required=False) or ""
    cta_button_text = ask("CTA button label",
                          default=ex.get("cta_button_text") or prev.get("cta_button_text", "Learn More"),
                          required=False) or ""

    # ── Editor's Note ─────────────────────────────────────────────────────────
    print()
    print("--- EDITOR'S NOTE ---")
    editor_note = ask("Editor's note (paste full text, end with a blank line if multiline)")
    # Allow multiline: keep reading until blank line if text doesn't end with period
    if not editor_note.endswith((".", "!", "?")):
        lines = [editor_note]
        print("  (continue typing, blank line to finish)")
        while True:
            line = input()
            if not line:
                break
            lines.append(line)
        editor_note = "\n".join(lines)

    return {
        "date":            str(target_date),
        "created_at":      datetime.utcnow().isoformat() + "Z",
        "status":          "ready",

        "signal_color":        signal_color,
        "signal_text":         signal_text,
        "signal_attribution":  "Licia Leslie",

        "level_resistance_2_label": r2_label,
        "level_resistance_2_value": r2_value,
        "level_resistance_1_label": r1_label,
        "level_resistance_1_value": r1_value,
        "level_key_label":          key_label,
        "level_key_value":          key_value,
        "level_support_1_label":    s1_label,
        "level_support_1_value":    s1_value,
        "level_support_2_label":    s2_label,
        "level_support_2_value":    s2_value,
        "levels_note":              levels_note,

        "the_number_value": the_number_value,
        "the_number_text":  the_number_text,

        "volume_anomaly_headline": volume_headline,
        "volume_anomaly_text":     volume_text,

        "editor_note_text": editor_note,

        "cta_headline":    cta_headline,
        "cta_body":        cta_body,
        "cta_url":         cta_url,
        "cta_button_text": cta_button_text,
    }


def save(data):
    os.makedirs(config.DAILY_BRIEF_DIR, exist_ok=True)
    path = os.path.join(config.DAILY_BRIEF_DIR, f"{data['date']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",  default=str(date.today()))
    parser.add_argument("--edit",  action="store_true",
                        help="Edit today's existing brief instead of starting fresh")
    args = parser.parse_args()

    existing = None
    if args.edit:
        path = os.path.join(config.DAILY_BRIEF_DIR, f"{args.date}.json")
        if os.path.exists(path):
            with open(path) as f:
                existing = json.load(f)
            print(f"Editing existing brief for {args.date}...")
        else:
            print(f"No existing brief for {args.date}, starting fresh.")

    data = collect(args.date, existing)
    path = save(data)

    print()
    print(f"Saved to: {path}")
    print()
    print("Ready. Now run the assembly engine:")
    print(f"  python3 scripts/assemble_newsletter.py --date {args.date} --dry-run")
    print(f"  python3 scripts/assemble_newsletter.py --date {args.date}")


if __name__ == "__main__":
    main()
