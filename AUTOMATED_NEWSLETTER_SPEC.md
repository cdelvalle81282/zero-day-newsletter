# Zero Day — Automated Newsletter Module Spec

**Version:** 1.0  
**Date:** April 16, 2026  
**Author:** Option Pit / Zero Day Team  
**For:** OptiPub CMS Implementation Team

---

## 1. Overview

The Zero Day is a daily 0DTE options newsletter sent each trading morning. The goal of this module is to automate the assembly and drafting of each issue using two sources:

1. **Structured editorial inputs** — a new "Daily Brief" form in OptiPub that Licia fills out each morning (~5 minutes)
2. **Market data** — pulled automatically from an external market data API (e.g., Polygon.io)

The module produces a **draft message** in OptiPub, ready for a final human review and one-click send. No issue goes out without editor approval.

---

## 2. Current Content Landscape (As-Is)

### Licia's 0DTE Daily Update
- **Publication:** `0DTE` (pub ID: 41, code: PDTE)
- **Message type:** `email-free-style` (type ID: 3)
- **Structure:** Drag-and-drop built HTML email containing:
  - A 2–4 paragraph teaser narrative (market commentary)
  - A "See more here" link pointing to the full post at `vip.optionpit.com/post/0dte-daily-{YYYY-MM-DD}`
  - Promotional sections (Live Room CTA, ads)
- **Key observation:** The detailed analysis — SPX levels, signal, trade breakdowns — lives in the **full VIP post**, not the teaser email. The teaser email is accessible via OptiPub's API (`GET /messages/{id}` → `data.content`), but the full post requires access to the VIP platform separately.

### The Zero Day Newsletter
- **Publication:** `LLF - Licia Leslie Franchise` (pub ID: 103, code: LLLF) — currently empty
- **Template file:** `TheZeroDay_Sample_Issue_3.html` (custom HTML, not drag-and-drop)
- **Sections:** See Section 4 below

---

## 3. Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Daily Automation Flow                         │
│                                                                  │
│  [Licia fills Daily Brief form] ──────────────────────┐         │
│  (5–10 structured fields, ~5 min)                     │         │
│                                                        ▼         │
│  [Market Data API] ──────────────────► [Assembly Engine]        │
│  (Polygon.io — SPX, VIX, SPY, QQQ,                  │          │
│   50-day MA, options volume)                          ▼          │
│                                          [Zero Day HTML Draft]   │
│                                                       │          │
│                                                       ▼          │
│                                          [OptiPub Draft Message] │
│                                          (pub 103, LLLF)         │
│                                                       │          │
│                                          [Editor reviews + sends]│
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Zero Day Section → Data Source Mapping

| Section | Source | Type | Notes |
|---|---|---|---|
| **Today's Signal** (Red/Yellow/Green) | Daily Brief form | Editorial input | Licia selects color + writes 2-sentence rationale |
| **Today's Levels** (SPX table) | Daily Brief form | Editorial input | Licia enters 5 price levels + labels |
| **The Number** (hero stat) | Daily Brief form | Editorial input | Licia enters % figure + one-paragraph explanation |
| **Volume Anomaly** | Daily Brief form + Market Data API | Hybrid | System pulls raw 0DTE volume; Licia adds narrative |
| **Editor's Note** | Daily Brief form | Editorial input | Licia writes 3-paragraph note (already written for the teaser email — can be reused) |
| **Market Snapshot** (SPX, VIX, SPY, QQQ) | Market Data API | Fully automated | Previous day closing prices + % change |
| **Date line** | System | Fully automated | Current date |
| **Top Ad / CTA blocks** | OptiPub (existing ads/macros) | Existing system | No change needed |
| **Footer** | Template constant | Static | No change |

**Sections requiring human input:** Signal, Levels, The Number narrative, Volume Anomaly narrative, Editor's Note (5 editorial fields + ~3 data entry fields)

**Sections fully automated:** Market Snapshot, date, ads, footer

---

## 5. New Content Type Required: "Daily Brief"

The CMS team needs to create a new structured content type (analogous to an `article` or `resource` in OptiPub) with the following fields. This is the primary request to the CMS implementation team.

### 5.1 Daily Brief Fields

```
daily_brief {
    // Identity
    publication_date        date          REQUIRED  // e.g. 2026-04-17
    publication_id          int           REQUIRED  // FK → 103 (LLLF)
    author_id               int           REQUIRED  // FK → Licia's author record

    // Today's Signal
    signal_color            enum          REQUIRED  // "red" | "yellow" | "green"
    signal_text             text (rich)   REQUIRED  // 2–4 sentences explaining the signal
    signal_attribution      string        default: "Licia Leslie"

    // Today's Levels (SPX)
    level_resistance_2_label  string      REQUIRED  // e.g. "Resistance 2 (50-day MA)"
    level_resistance_2_value  decimal     REQUIRED  // e.g. 6850
    level_resistance_1_label  string      REQUIRED  // e.g. "Resistance 1"
    level_resistance_1_value  decimal     REQUIRED
    level_key_label           string      REQUIRED  // e.g. "Key Level (Pitchfork)"
    level_key_value           decimal     REQUIRED
    level_support_1_label     string      REQUIRED  // e.g. "Support 1"
    level_support_1_value     decimal     REQUIRED
    level_support_2_label     string      REQUIRED  // e.g. "Support 2"
    level_support_2_value     decimal     REQUIRED
    levels_note               text        OPTIONAL  // 1–2 sentence commentary below levels table

    // The Number
    the_number_value         string       REQUIRED  // e.g. "+4,700%"
    the_number_text          text (rich)  REQUIRED  // explanation paragraph

    // Volume Anomaly
    volume_anomaly_headline  string       REQUIRED  // e.g. "SPX 0DTE Volume: 2.8M Contracts"
    volume_anomaly_text      text (rich)  REQUIRED  // narrative

    // Editor's Note
    editor_note_text         text (rich)  REQUIRED  // 3 paragraphs

    // Status
    status                   enum         default: "draft"  // "draft" | "ready" | "sent"
}
```

### 5.2 Suggested UI Layout

The form should be a single-page vertical layout with collapsible sections, pre-populated with yesterday's level labels so Licia only needs to update values. Estimated fill time: 5–8 minutes.

---

## 6. Market Data API Integration

### Required Data Points (pulled automatically)

| Field | Source | Endpoint Example |
|---|---|---|
| SPX previous close + % change | Polygon.io or Alpha Vantage | `GET /v2/aggs/ticker/SPX/range/1/day/...` |
| VIX previous close + % change | Polygon.io | `GET /v2/aggs/ticker/VIX/range/1/day/...` |
| SPY previous close + % change | Polygon.io | `GET /v2/aggs/ticker/SPY/range/1/day/...` |
| QQQ previous close + % change | Polygon.io | `GET /v2/aggs/ticker/QQQ/range/1/day/...` |
| SPX 50-day moving average | Calculated from price history | Rolling 50-day close average |
| 0DTE SPX total options volume | Polygon.io Options API | `GET /v3/snapshot/options/SPX` filtered by expiration = today |
| 0DTE volume vs. 20-day avg | Derived | Calculate from 20 prior trading days |

### Recommended Provider

**Polygon.io** — Stocks Starter tier (~$29/mo) covers equities + options snapshot. Upgrade to Options tier (~$79/mo) needed for intraday strike-level options volume. All endpoints are REST + JSON.

### Data Pull Schedule

Market data should be pulled **after market close** (after 4:30 PM ET) for the previous trading day and stored alongside the Daily Brief record. This allows the automation to run reliably the next morning regardless of API availability.

---

## 7. Assembly Engine

The assembly engine is a script (Python recommended) that:

1. **Queries OptiPub** for the Daily Brief record with `status = "ready"` and `publication_date = today`
2. **Fetches cached market data** for the previous trading day
3. **Renders the Zero Day HTML template** by substituting values into named placeholders (see Section 8)
4. **Creates a draft message** in OptiPub via `POST /messages` with:
   - `publication_id`: 103 (LLLF)
   - `message_type_id`: 1 (email-article) or 3 (email-free-style)
   - `title`: derived from signal color + date, e.g., "Zero Day — Yellow Light — April 17"
   - `content`: the rendered HTML
   - `status`: draft (not scheduled)
5. **Notifies the editor** (via email or Slack) that the draft is ready for review

### Trigger

Run daily at **7:30 AM ET** on trading days. If no Daily Brief with `status = "ready"` exists for today, send an alert to the editor rather than creating a draft.

---

## 8. HTML Template Placeholders

The `TheZeroDay_Sample_Issue_3.html` template should be updated to use named placeholder tokens. Suggested token syntax: `{{TOKEN_NAME}}`

| Token | Source | Example Value |
|---|---|---|
| `{{ISSUE_DATE_LONG}}` | System | `Wednesday, April 17, 2026` |
| `{{SIGNAL_COLOR_HEX}}` | Daily Brief → signal_color | `#D4A017` (yellow), `#22C55E` (green), `#CC3333` (red) |
| `{{SIGNAL_ICON}}` | Daily Brief → signal_color | `⚠` (yellow), `✓` (green), `✗` (red) |
| `{{SIGNAL_LABEL}}` | Daily Brief → signal_color | `YELLOW LIGHT` |
| `{{SIGNAL_TEXT}}` | Daily Brief | _(Licia's 2-4 sentence rationale)_ |
| `{{SIGNAL_ATTRIBUTION}}` | Daily Brief | `Licia Leslie` |
| `{{LEVELS_DATE}}` | System | `Thursday, April 17` |
| `{{LEVEL_R2_LABEL}}` | Daily Brief | `Resistance 2 (50-day MA)` |
| `{{LEVEL_R2_VALUE}}` | Daily Brief | `6,850` |
| `{{LEVEL_R1_LABEL}}` | Daily Brief | `Resistance 1` |
| `{{LEVEL_R1_VALUE}}` | Daily Brief | `6,784` |
| `{{LEVEL_KEY_LABEL}}` | Daily Brief | `Key Level (Pitchfork)` |
| `{{LEVEL_KEY_VALUE}}` | Daily Brief | `6,767` |
| `{{LEVEL_S1_LABEL}}` | Daily Brief | `Support 1` |
| `{{LEVEL_S1_VALUE}}` | Daily Brief | `6,753` |
| `{{LEVEL_S2_LABEL}}` | Daily Brief | `Support 2` |
| `{{LEVEL_S2_VALUE}}` | Daily Brief | `6,741` |
| `{{LEVELS_NOTE}}` | Daily Brief | _(optional 1–2 sentences)_ |
| `{{THE_NUMBER}}` | Daily Brief | `+4,700%` |
| `{{THE_NUMBER_TEXT}}` | Daily Brief | _(explanation paragraph)_ |
| `{{VOLUME_HEADLINE}}` | Daily Brief | `SPX 0DTE Volume: 2.8M Contracts` |
| `{{VOLUME_TEXT}}` | Daily Brief | _(narrative paragraph)_ |
| `{{EDITOR_NOTE_TEXT}}` | Daily Brief | _(3 paragraphs)_ |
| `{{SNAP_SPX_VALUE}}` | Market Data API | `6,782.81` |
| `{{SNAP_SPX_PCT}}` | Market Data API | `+2.51%` |
| `{{SNAP_SPX_COLOR}}` | Market Data API | `#22C55E` (green) or `#CC3333` (red) |
| `{{SNAP_VIX_VALUE}}` | Market Data API | `21.04` |
| `{{SNAP_VIX_PCT}}` | Market Data API | `-18.4%` |
| `{{SNAP_VIX_COLOR}}` | Market Data API | _(inverted: VIX down = green)_ |
| `{{SNAP_SPY_VALUE}}` | Market Data API | `676.28` |
| `{{SNAP_SPY_PCT}}` | Market Data API | `+2.48%` |
| `{{SNAP_SPY_COLOR}}` | Market Data API | `#22C55E` |
| `{{SNAP_QQQ_VALUE}}` | Market Data API | `493.52` |
| `{{SNAP_QQQ_PCT}}` | Market Data API | `+2.94%` |
| `{{SNAP_QQQ_COLOR}}` | Market Data API | `#22C55E` |

---

## 9. OptiPub API Calls Required

The CMS team needs to ensure these API endpoints are available and documented:

### Read (existing endpoints)

```
GET  /api/3.2/contents/resources?publication_id=103        — list Daily Brief records
GET  /api/3.2/contents/resources/{id}                      — get single Daily Brief
GET  /api/3.2/messages/{id}                                 — get 0DTE teaser email (if reusing editor note)
```

### Write (for draft creation)

```
POST /api/3.2/messages                                      — create draft Zero Day message
PATCH /api/3.2/messages/{id}                                — update draft if re-run
POST /api/3.2/messages/{id}/schedule                        — schedule for send (post-review)
```

### New endpoints needed (CMS to build)

```
POST /api/3.2/daily-briefs                                  — create a new Daily Brief record
GET  /api/3.2/daily-briefs?publication_date={date}          — get brief for a given date
PATCH /api/3.2/daily-briefs/{id}                            — update (e.g. set status to "ready")
```

Alternatively, the Daily Brief can be modeled as a `resource` (article) with a custom `resource_category` and metadata fields stored as JSON in a `data` column — this avoids new table creation if OptiPub supports flexible resource metadata.

---

## 10. Licia's Daily Workflow (Future State)

| Time | Action | Where |
|---|---|---|
| Pre-market (~8:00 AM ET) | Open Daily Brief form, fill in Signal, Levels, The Number, Volume note, Editor's Note | OptiPub CMS — new "Daily Brief" UI |
| ~8:10 AM ET | Click "Mark as Ready" | OptiPub CMS |
| 8:30 AM ET | Automation runs — draft Zero Day created | Automated |
| ~8:30 AM ET | Review draft in OptiPub, approve + schedule for send | OptiPub CMS |
| 8:45 AM ET | Zero Day sends to subscribers | Automated |

---

## 11. Phase Breakdown for CMS Implementation

### Phase 1 — Foundation (CMS team)
- [ ] Create `daily_brief` content type (or flexible resource metadata model) with the fields in Section 5
- [ ] Build the Daily Brief input form UI in OptiPub
- [ ] Expose `GET /api/3.2/daily-briefs?publication_date={date}` endpoint
- [ ] Update `TheZeroDay_Sample_Issue_3.html` to use `{{PLACEHOLDER}}` tokens

### Phase 2 — Market Data (external/dev team)
- [ ] Set up Polygon.io API key
- [ ] Build nightly market data fetch job (runs after 4:30 PM ET)
- [ ] Store SPX/VIX/SPY/QQQ closes and 0DTE volume in a `market_data_cache` table or similar

### Phase 3 — Assembly Engine (external/dev team)
- [ ] Build Python assembly script
- [ ] Token substitution from Daily Brief + market data into HTML template
- [ ] `POST /messages` draft creation in OptiPub
- [ ] Editor notification (email or Slack webhook)
- [ ] Schedule via cron (7:30 AM ET, trading days only)

### Phase 4 — Testing & Handoff
- [ ] 2-week parallel run (manual + automated, compare outputs)
- [ ] Sign-off from Licia on Daily Brief form UX
- [ ] Monitor for trading day calendar edge cases (holidays, early closes)

---

## 12. Open Questions for CMS Team

1. **Can OptiPub support custom field metadata on resources/articles**, or does a new table need to be created for `daily_brief`?
2. **Is there an existing scheduled-job infrastructure** in OptiPub that the assembly engine can hook into, or does this need to be an external cron?
3. **Draft message creation** — does `POST /messages` support setting `status: draft` (not scheduled, not sent)?
4. **Does vip.optionpit.com** share a database or API with OptiPub? If yes, could the full 0DTE post content (at `vip.optionpit.com/post/0dte-daily-{date}`) be read via the OptiPub API, potentially replacing some Daily Brief form fields with auto-pull from the VIP post?
5. **Confirm Zero Day publication**: Should Zero Day messages be sent from pub 103 (LLF - Licia Leslie Franchise) or a new dedicated publication?

---

## 13. Out of Scope

- Changes to Licia's existing 0DTE daily update email workflow (pub 41, PDTE)
- Subscriber management or segmentation
- A/B testing of subject lines
- Monetization / paywall logic
- Any section of the Zero Day not listed in Section 4
