# AIS-tree Fellowship Updater

## Project context

This tool keeps the AIS-tree fellowship directory's Google Sheet up to date. AIS-tree is a filterable directory of AI safety opportunities — courses, fellowships, workshops — aimed at newcomers to the EA/AI-safety space. The data lives in a Google Sheet that's published as CSV and read live by a single-file React app in a separate repo. This updater is a separate Streamlit application that detects when fellowship websites change, asks Claude to propose field-level updates, and lets a human reviewer accept or reject those proposals before they're written back to the sheet.

This is the "updater" half of a planned two-tool system. The other half (detector for finding new fellowships from aggregator sites) will live in its own repo and is out of scope here.

## Environment and execution constraints

**This Claude Code user does not have sudo.** If you need to install a system package (`apt install ...`), Docker, or anything else requiring elevated privileges: **stop, print the exact command(s) the human needs to run, and wait.** Do not attempt `sudo` calls — they will fail and burn a turn.

**Python is managed via conda.** The conda environment for this project is named **`ais-tree`**. Always activate it before running any Python command:

```bash
conda activate ais-tree
```

If a Python package is missing, install it into the active env:

```bash
conda install -c conda-forge <package>
# or, if not on conda-forge:
pip install <package>
```

```bash
conda create -n ais-tree python=3.11
conda activate ais-tree
conda install -c conda-forge gspread streamlit trafilatura anthropic requests
```

**Do not touch system Python.** Everything runs inside the conda env.

## Architecture decisions

- **Streamlit Community Cloud** for hosting. Free tier; requires public GitHub repo.
- **Google Sheet is the source of truth**, accessed via gspread + service account.
- **All state lives in the same sheet** in a `_updater_state` tab. Streamlit Cloud's filesystem is ephemeral, so we cannot persist anything in local files between runs.
- **Hash-based change detection is code-only.** The LLM is never asked "did this page change?" — that's a deterministic comparison of trafilatura-extracted content hashes against the stored hash in `_updater_state`.
- **LLM is used only for two things:** (1) proposing new field values from changed page content, (2) classifying the overall change as `highly_relevant`, `potentially_relevant`, or `not_relevant`. Per-field diffing (old value vs proposed new value) is done in code via normalized string comparison.
- **Human acceptance is required for every write to the main sheet.** No auto-accept in v1, even for `highly_relevant`.
- **Schema lives in `config.json` in this repo**, not derived from sheet headers. Schema changes are versioned in git and reviewable in PRs.

## Tech stack

- Python 3.11 in conda env `ais-tree`
- `streamlit` for the UI
- `gspread` (v6.x) for Google Sheets read/write via service account
- `trafilatura` for main-content extraction from fetched HTML
- `requests` for fetching (Playwright fallback noted as future work, not v1)
- `anthropic` SDK for the LLM calls
- Model: `claude-sonnet-4-6` (verify current latest Sonnet when starting; pin in `config.json`)

## File layout

```
ais-tree-updater/
├── app.py                  # Streamlit entry point
├── updater/
│   ├── __init__.py
│   ├── fetch.py            # HTTP fetch + trafilatura extraction + hashing
│   ├── sheets.py           # gspread wrapper, read main + state tab, writes
│   ├── llm.py              # Anthropic API call + response parsing
│   ├── diff.py             # field-level normalization, comparison, type validation
│   └── pipeline.py         # fetch_row (phase 1) + process_changed_row (phase 2)
├── prompts/
│   └── classify.md         # system prompt for the classification call
├── scripts/
│   ├── split_aisdb003.py   # one-time: split Frontier AI Governance into two rows
│   └── migrate_numeric_fields.py  # one-time: converted duration/time_commitment to numeric
├── config.json             # schema, field metadata, fetch settings, model name
├── requirements.txt
├── .streamlit/
│   └── secrets.toml        # LOCAL ONLY — gitignored
├── .gitignore              # excludes secrets.toml and service_account.json
└── README.md
```

## Data schema

The main sheet has 27 columns. Each fellowship is one row, identified by a stable `ID` like `aisdb_001`. Columns grouped by volatility:

**Identity (never changes after creation):** `ID`, `url`

**Stable (changes rare, ~yearly):** `name`, `organization`, `track`, `program_type`, `format`, `pacing`, `geographic_focus`, `for_student`, `for_early_career`, `for_mid_career`, `for_senior`, `for_career_switch`

**Semi-stable (changes occasional):** `description`, `duration`, `time_commitment`, `cost`, `recompensation`, `prerequisites`, `tags`

**Volatile (changes per cohort/cycle — the main reason this tool exists):** `application_status`, `next_deadline`, `next_cohort_start`

**Auto-managed by the updater:** `last_verified` — set to today's date whenever a row is processed.

**Never modified by the updater:** `notes` — user-only field.

**Never modified by the updater, read by the LLM:** `notes_for_claude_during_update` — per-row hints passed verbatim into the LLM prompt alongside the page content. Use this for known extraction quirks, URL instability warnings, or anything the LLM should know about a specific fellowship. Currently populated for:
- `aisdb_001–003` (BlueDot): warns that deadlines are in the `__NEXT_DATA__` Next.js JSON blob, not in trafilatura-extracted text
- `aisdb_012` (Anthropic Fellows): warns that the URL is year-specific and a 404 means the cycle ended, not that applications are closed

### Field types (enforced in `config.json` and validated in `diff.py`)

| Type | Fields | Notes |
|---|---|---|
| `string` | name, organization, description, cost, prerequisites | free text |
| `binary_int` | for_student, for_early_career, for_mid_career, for_senior, for_career_switch | 0 or 1 only |
| `date_or_unclear` | next_deadline, next_cohort_start | YYYY-MM-DD or `[unclear]` |
| `comma_separated` | tags | sorted, lowercase |
| `usd_int` | recompensation | integer USD, no symbols (e.g. `8400`); `0` = none; `[unclear]` = unknown |
| `weeks_int` | duration | integer weeks (e.g. `10`); `<1` for sub-week programs; `[unclear]` if unknown |
| `hrs_per_week_int` | time_commitment | integer hours/week (e.g. `10`); `40` = full-time; `[unclear]` if unknown |
| `string` + `allowed_values` | track, program_type, format, pacing, geographic_focus, application_status | enum-validated; see `config.json` for the allowed lists |

**Enum allowed values:**
- `track`: technical, governance, general, neutral
- `program_type`: fellowship, course, workshop, mentorship, bootcamp, advising
- `format`: online, in-person, hybrid
- `pacing`: self-paced, cohort-based (note: rolling admissions is still "cohort-based")
- `geographic_focus`: global, us-centric, uk/eu-centric
- `application_status`: open, closed, rolling (plus `[unclear]`)

Any field may hold `[unclear]` — filling it in counts as a meaningful change.

## State tab (`_updater_state`)

Auto-created on first run if missing. `ensure_state_tab()` also backfills any columns added after the initial creation (so adding a column to `STATE_COLUMNS` is safe). Columns:

- `id` — matches `ID` in main sheet
- `url` — denormalized for sanity-checking
- `last_hash` — sha256 of the trafilatura-extracted text
- `last_checked_at` — ISO timestamp
- `last_classification` — `highly_relevant` / `potentially_relevant` / `not_relevant` / `unchanged` / `fetch_error`
- `last_result` — `accepted` / `rejected` / `pending` / null
- `trigger_check_next_update` — if `"true"`, the next run forces this row through the LLM even if the hash hasn't changed, then clears the flag. Used to re-verify rows whose current values are known approximations. Set manually or by migration scripts.

**19 rows currently have `trigger_check_next_update = true`** (set during the numeric field migration — ranges were converted to max values and need LLM verification). They will auto-clear on next updater run.

## Pipeline

The pipeline is split into two phases, both visible in the UI before any LLM costs are incurred.

**Phase 1 — fetch (cheap):** For each row, fetch the URL, extract text with trafilatura, hash it. Compare against `last_hash` in state. Also check `trigger_check_next_update`.
- If hash matches AND no trigger → `unchanged`. State updated, `last_verified` set. No LLM call.
- If hash changed OR trigger is set → `changed`. Passes to phase 2.
- On fetch/network error → `fetch_error`. State updated.

**Phase 2 — LLM (confirmed by user):** After phase 1, the UI shows a summary (N changed, N unchanged, N errors) and an estimated cost (~3¢/entry). User must click "Analyse N entries" to proceed.
- LLM receives: today's date, current row JSON, extracted page text, schema with allowed values listed per field.
- LLM returns structured JSON: `classification`, `reasoning`, `proposed_fields`, `snippets`.
- Code-level diff compares proposed values against current values using type-aware normalization.
- Type validation runs on all proposed values — warnings surfaced in the review card.
- State updated with new hash, classification, `last_result = pending`. Trigger flag cleared.

**Review (per card):** Accept / Reject / Edit-then-accept. Accept writes proposed values to the main sheet. Triggered rows show "_(forced re-check)_" label.

## LLM contract

- **Model:** `claude-sonnet-4-6` (verify current latest when starting; pinned in `config.json`)
- **System prompt** lives in `prompts/classify.md` — edit there without redeploying.
- **User prompt** includes: `Today's date: YYYY-MM-DD`, current row JSON, page text (first 8000 chars), schema description (field name, type, volatility, description, allowed values if any), and any `notes_for_claude_during_update` for this row.
- **Response must be strict JSON:**

```json
{
  "classification": "highly_relevant | potentially_relevant | not_relevant",
  "reasoning": "One sentence explaining the classification.",
  "proposed_fields": { "field_name": "new value" },
  "snippets": { "field_name": "exact supporting text from the page" }
}
```

- On JSON parse failure: mark `llm_error`, store raw output, surface in UI — do not crash.
- Prompt cache (`cache_control: ephemeral` on system block) gives within-run savings across multiple rows. Zero benefit across separate runs.

## Type validation system

`diff.py` contains:
- `normalize(value, field_type)` — canonical form for comparison (strips units, lowercases, parses dates, etc.)
- `validate_value(value, field_type, allowed_values=None)` — returns a warning string or None
- `validate_proposed(proposed_fields, field_schema)` — returns list of `{field, value, warning}` for all type violations in an LLM response

Type warnings are displayed in review cards above the diff table. The LLM prompt includes allowed values explicitly so violations should be rare.

## Classification taxonomy

- **`highly_relevant`** — change to a volatile field (deadline, application status, cohort start) OR a field currently `[unclear]` being filled in OR a material change to cost/prerequisites/duration that would affect someone's decision to apply.
- **`potentially_relevant`** — ambiguous change, partial information, or a change to a semi-stable field (description rewording, tag adjustment).
- **`not_relevant`** — extracted content changed but the underlying facts did not (page redesign, marketing copy edits, navigation changes). Use only when confident.

When uncertain, prefer **potentially_relevant**.

## UI flow

1. **Login** — password gate (single shared password from `st.secrets`)
2. **"Run updater"** — triggers phase 1 (fetch all pages). Shows progress with `st.status`.
3. **Phase 1 summary** — metrics (Changed / Unchanged / Errors / No URL), error details, estimated LLM cost.
4. **"Analyse N entries"** — triggers phase 2 (LLM calls for changed rows only).
5. **Results** — cards grouped by classification (highly_relevant first). Each card shows:
   - Name + URL, classification badge, reasoning, "(forced re-check)" if triggered
   - Type warnings (if any proposed value failed type validation)
   - Diff table (field / current / proposed / source snippet)
   - Accept / Reject / Edit-then-accept buttons
6. **"Run updater again"** — resets all phase state for a fresh run.
7. Unchanged rows shown in collapsed expander. No-URL and fetch-error rows similarly collapsed.

## Local development workflow

```bash
conda run -n ais-tree streamlit run app.py --server.port 8501 --server.headless true
```

Opens `localhost:8501`. Auto-reloads on file save. Test against the real sheet — there is no staging sheet. Acceptance writes are gated behind the human Accept button.

## Deployment

- Streamlit Community Cloud, deploying from `main` branch of the public GitHub repo.
- Secrets (in Streamlit Cloud dashboard, TOML format — same content as local `.streamlit/secrets.toml`):
  - `[gcp_service_account]` — full service account JSON as TOML. `private_key` must preserve `\n` as literal `\n`.
  - `ANTHROPIC_API_KEY`, `SHEET_ID`, `APP_PASSWORD` — must appear **before** `[gcp_service_account]` in the TOML file (TOML absorbs everything after a section header into that section).
- `requirements.txt`: streamlit, gspread, google-auth, trafilatura, anthropic, requests.
- Push to `main` triggers auto-redeploy in ~60 seconds.

## Non-negotiables

- **Never auto-write to the main sheet.** Every change requires a human Accept click.
- **Never commit credentials.** `service_account.json`, `.streamlit/secrets.toml` in `.gitignore`. Verify before every commit.
- **Never modify the main sheet's structure** (column add/remove/rename) from this tool. Schema changes are manual + a `config.json` update.
- **The published-to-web CSV that feeds the live website reads from the main data tab.** Writes go live within 5–10 minutes. Treat writes as production changes.
- **`_updater_state` tab is internal.** Don't expose it in the live website's CSV.
- **No `sudo`.** Report needed system packages to the human and stop.

## Out of scope for v1

- Detector functionality (lives in a separate repo when built)
- Scheduled/automatic runs
- Multi-user accounts or role-based auth
- Bulk accept/reject
- Playwright fallback for JS-rendered pages
- Webhook notifications
- Sheet history / undo

## Known extraction problems

### BlueDot (bluedot.org) — aisdb_001, aisdb_002, aisdb_003

Deadline and cohort-start dates are visible on the page but **not extracted by trafilatura** — they are embedded in a Next.js JSON payload (`__NEXT_DATA__`) and rendered client-side. Trafilatura sees the label "Schedule" but not the dates. A fix would parse `__NEXT_DATA__` directly from the raw HTML. Not implemented in v1 — BlueDot deadlines must be checked manually. The `notes_for_claude_during_update` column for these rows warns the LLM.

### ARENA (aisdb_008)

Moved from `bluedot.org/arena` (404) to `bluedot.org/courses/arena`. URL corrected. Same Next.js extraction caveat as other BlueDot entries.

### OpenAI Residency (aisdb_018)

Produced a `fetch_error` in the first live test. URL returns 200 in isolation — likely a transient rate-limit or bot-detection issue. If persistent, needs Playwright fallback (out of scope for v1).

### Frontier AI Governance (aisdb_003) — PENDING SPLIT

BlueDot's Frontier AI Governance page covers two programs: a 5-day intensive and a 5-week part-time track. `scripts/split_aisdb003.py` is written and ready to run — it will rename aisdb_003 to the intensive track and append aisdb_003b for part-time. **Has not been run yet.** After running, both rows need manual field updates (name, duration, time_commitment, format, pacing, URL if different, deadlines).

## Backlog

### Audit log tab
`config.json` has `audit_log_enabled: true` but logging to a `_audit` sheet tab is not yet implemented. Useful for debugging the first few weeks of live use.

### "Needs follow-up" state
Currently only Accept / Reject. A third state (e.g. "flag for manual check") would help when the LLM detects a real change but the proposed values are wrong.

### Playwright fallback for JS-heavy pages
`requests` + trafilatura misses JS-rendered content. BlueDot deadlines are the main known case. Playwright would solve this but is out of scope for v1.

### cost field
Only one entry currently has a value (`0`). The field type is still free-text `string`. Consider making it `usd_int` like `recompensation` once more data is present.

## ⬅ Next step (start here next session)

**Launch locally and test the new features:**

```bash
conda run -n ais-tree streamlit run app.py --server.port 8501 --server.headless true
```

Things to verify:
1. **Two-phase UX** — "Run updater" shows fetch summary before any LLM calls; cost estimate shown; "Analyse N entries" button triggers phase 2.
2. **Triggered rows** — 19 rows are flagged `trigger_check_next_update = true` from the numeric field migration. They should appear in the "changed" count even if page content hasn't changed, and show "_(forced re-check)_" in their cards. After accepting/rejecting, re-run and confirm they no longer appear.
3. **Type warnings** — if the LLM proposes a bad value (e.g. "10 weeks" instead of "10" for duration), a yellow warning should appear above the diff table.
4. **Run `scripts/split_aisdb003.py`** to split Frontier AI Governance into two rows, then manually update field values.
