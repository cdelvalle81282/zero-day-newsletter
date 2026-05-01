# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

An automated daily email newsletter pipeline for **0DTE Daily** by Option Pit. Each trading day, Licia fills out a brief form, market data is fetched from Polygon.io, and the assembly engine renders an HTML newsletter and posts it as a draft to OptiPub (the ESP) for review and send.

## Running the server

```bash
# Local dev (port 8001)
python server/app.py

# Docker
docker compose build
docker compose up -d

# Deploy to DigitalOcean
bash deploy.sh
```

The web UI lives at `http://localhost:8001/0dte-daily/` and requires basic auth (`ZERODAY_PASSWORD`).

## Running scripts manually

```bash
# Fill Daily Brief interactively (CLI alternative to web form)
python scripts/daily_brief.py

# Fetch market data from Polygon.io (options chain + quotes)
python scripts/fetch_market_data.py --mode full

# Assemble newsletter and create OptiPub draft
python scripts/assemble_newsletter.py --date YYYY-MM-DD

# Dry-run assembly (renders HTML locally, does NOT post to OptiPub)
python scripts/assemble_newsletter.py --date YYYY-MM-DD --dry-run
```

No test suite exists. The dry-run flag is the primary way to validate assembly changes safely.

## Architecture

All data is stored as flat JSON files — no database.

```
daily_briefs/{date}.json    Editorial inputs from Licia
market_data/{date}.json     Polygon.io quotes + 0DTE options chain
market_data/premarket_*.json Pre-market SPX snapshots (6 AM ET)
drafts/{date}.html          Rendered newsletter HTML
drafts/{date}.approved      OptiPub draft ID + approval timestamp
drafts/charts/{date}.*      Uploaded levels chart images
```

### Daily pipeline (automated via APScheduler in `server/app.py`)

| Time (ET) | Job |
|-----------|-----|
| 6:00 AM   | Fetch pre-market SPX price |
| 9:00 AM   | Polygon API health check |
| 9:15 AM   | Morning check — alert if brief is missing |
| 3:50 PM   | Fetch 0DTE options chain |
| 4:35 PM   | Fetch EOD quotes, auto-assemble if brief exists |

### Key files

| File | Role |
|------|------|
| `server/app.py` | Flask routes, APScheduler jobs, all HTTP endpoints |
| `scripts/config.py` | All configuration — env vars with defaults |
| `scripts/assemble_newsletter.py` | Token map builder, HTML renderer, OptiPub draft poster |
| `scripts/fetch_market_data.py` | Polygon.io data fetch (2-phase: options then quotes) |
| `TheZeroDay_Sample_Issue_3.html` | The email template — contains `{{TOKEN}}` placeholders |
| `server/templates/preview.html` | Web UI for draft review and OptiPub approval |

### Token system

`assemble_newsletter.py:build_tokens()` builds a dict mapping `{{TOKEN_NAME}}` to values from the Daily Brief JSON and market data. `render_template()` does a simple string substitution into the HTML template. Tokens are documented in `AUTOMATED_NEWSLETTER_SPEC.md` Section 8.

OptiPub macros (`{$custom_...}`, `{$email}`, `{$custom_unsub}`) are passed through untouched — OptiPub fills them at send time.

### Subject line generation

`GET /0dte-daily/suggest-subject/<date>` calls Claude (Haiku) with signal + editor's note context and returns `{subject, preview_line}`. This feeds the test-send modal in the preview UI. The actual OptiPub draft subject is set separately inside `create_optipub_draft()`.

### Notifications

Slack webhook (or Gmail fallback) is used for pipeline alerts — missing brief, assembly failure, approval confirmation. Configured via `NOTIFY_METHOD` + `SLACK_WEBHOOK_URL` in env.

## Required environment variables

See `scripts/config.py` for all defaults. These must be set in production (`.env` or Docker env):

| Variable | Required | Purpose |
|----------|----------|---------|
| `ZERODAY_PASSWORD` | Yes | Basic auth for the web UI |
| `POLYGON_API_KEY` | Yes | Market data fetching |
| `OPTIPUB_API_KEY` | Yes | Posting drafts to OptiPub |
| `ANTHROPIC_API_KEY` | No | Claude subject line suggestions (falls back to defaults) |
| `SLACK_WEBHOOK_URL` | No | Pipeline notifications |
