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
│   ├── diff.py             # field-level normalization + comparison
│   └── pipeline.py         # orchestration of fetch → diff → llm → propose
├── prompts/
│   └── classify.md         # system prompt for the classification call
├── config.json             # schema, field metadata, fetch settings, model name
├── requirements.txt
├── .streamlit/
│   └── secrets.toml        # LOCAL ONLY — gitignored
├── .gitignore              # MUST exclude secrets.toml and service_account.json
└── README.md
```

## Data schema

The main sheet has 26 columns. Each fellowship is one row, identified by a stable `ID` like `aisdb_001`. Columns grouped by volatility:

**Identity (never changes after creation):** `ID`, `url`

**Stable (changes rare, ~yearly):** `name`, `organization`, `track`, `program_type`, `format`, `pacing`, `geographic_focus`, `for_student`, `for_early_career`, `for_mid_career`, `for_senior`, `for_career_switch`

**Semi-stable (changes occasional):** `description`, `duration`, `time_commitment`, `cost`, `recompensation`, `prerequisites`, `tags`

**Volatile (changes per cohort/cycle — the main reason this tool exists):** `application_status`, `next_deadline`, `next_cohort_start`

**Auto-managed by the updater:** `last_verified` — set to today's date whenever a row is processed (regardless of whether anything else changed).

**Never modified by the updater:** `notes` — user-only field.

**Never modified by the updater, read by the LLM:** `notes_for_claude_during_update` — per-row hints passed verbatim into the LLM prompt alongside the page content. Use this for known extraction quirks (e.g. JS-rendered dates), URL instability warnings, or anything the LLM should know about a specific fellowship that isn't derivable from the page. Currently populated for:
- `aisdb_001–003` (BlueDot): warns that deadlines are in the `__NEXT_DATA__` Next.js JSON blob, not in trafilatura-extracted text
- `aisdb_012` (Anthropic Fellows): warns that the URL is year-specific and a 404 means the cycle ended, not that applications are closed

Field-type notes for the LLM contract:
- `for_*` columns are binary integers `0` or `1`
- `next_deadline` and `next_cohort_start` are dates or `[unclear]`
- `tags` is a comma-separated list
- Any field may currently hold the literal string `[unclear]` meaning "not yet verified" — filling these in counts as a meaningful (potentially relevant) change if the page provides the info

## State tab (`_updater_state`)

Auto-created on first run if missing. Columns:

- `id` — matches `ID` in main sheet
- `url` — denormalized for sanity-checking
- `last_hash` — sha256 of the trafilatura-extracted text
- `last_checked_at` — ISO timestamp
- `last_classification` — `highly_relevant` / `potentially_relevant` / `not_relevant` / `unchanged` / `fetch_error`
- `last_result` — `accepted` / `rejected` / `pending` / null

## Pipeline

For each row in the main sheet:

1. **Fetch** the URL. Handle HTTP errors gracefully — mark `fetch_error` in state, surface in UI, skip LLM call.
2. **Extract** main content with trafilatura.
3. **Hash** the extracted text (sha256).
4. **Compare** against `last_hash` in state. If equal → mark `unchanged`, update `last_checked_at`, set `last_verified` in main sheet to today, done.
5. If changed → **call the LLM** with: the old row as JSON, the new extracted text, and the field schema with descriptions. LLM returns a structured JSON response (see LLM contract below).
6. **Code-level diff:** for each field in the LLM's `proposed_fields`, compare against the current sheet value using normalized comparison (strip whitespace, normalize date formats, case-insensitive for categorical fields). Only fields where normalized values differ count as "proposed changes."
7. **Build a review item:** classification, list of proposed changes (each with old value, proposed value, source snippet from LLM), and the URL.
8. **Display as a card in the UI.** Do not write anything to the main sheet yet. Update state with new hash and `last_classification`, set `last_result = pending`.
9. **On Accept:** write proposed values to the main sheet, set `last_verified` to today, set `last_result = accepted` in state.
10. **On Reject:** make no changes to the main sheet, set `last_result = rejected` in state. The hash is already updated, so the page won't re-flag until it changes again.
11. **On Edit:** user modifies proposed values in the card before accepting; treated as Accept with the edited values.

## LLM contract

- **Model:** `claude-sonnet-4-6` (verify current latest when starting)
- **System prompt** lives in `prompts/classify.md` and is loaded at startup (not inlined in code), so it can be iterated on without redeploying.
- **User prompt** is constructed per call and includes the current row JSON, the new page text, and a compact schema description.
- **Response must be strict JSON** with this shape:

```json
{
  "classification": "highly_relevant | potentially_relevant | not_relevant",
  "reasoning": "one-sentence justification",
  "proposed_fields": {
    "field_name": "new value"
  },
  "snippets": {
    "field_name": "the exact text from the page that supports this value"
  }
}
```

- On JSON parse failure: mark the row as `needs_manual_review`, store the raw LLM output, surface in UI with the raw text — do not crash or silently drop.
- Within a single run, sequential calls reuse the same system prompt and schema; rely on Anthropic's 5-minute prompt cache TTL by setting `cache_control` on the system block. This is within-run caching only — zero benefit across weekly runs.

## Classification taxonomy

- **`highly_relevant`** — change to a volatile field (deadline, application status, cohort start) OR a field currently `[unclear]` being filled in OR a material change to cost/prerequisites/duration that would affect someone's decision to apply.
- **`potentially_relevant`** — ambiguous change, partial information, or a change to a semi-stable field (description rewording, tag adjustment).
- **`not_relevant`** — extracted content changed but the underlying facts didn't. Page redesign, marketing copy edits, navigation changes, etc.

The LLM should err toward `potentially_relevant` when uncertain — `not_relevant` is only for high-confidence "nothing substantive changed."

## UI requirements

- **Password gate** on app entry — single shared password from `st.secrets`, blocks all functionality until entered.
- **"Run updater" button** triggers the full pipeline; show progress as `Checking N/M...` using `st.status`.
- **Results view** shows cards grouped by classification (`highly_relevant` first, then `potentially_relevant`, then collapsed `not_relevant` and `unchanged` summaries).
- **Each card** has: fellowship name + URL, classification badge, diff table (old → proposed, per field), source snippet per change, and three buttons: Accept, Reject, Edit.
- **Filter** to show only `pending` items vs. include already-reviewed.
- **No real-time streaming** of LLM output — just final results per row.

## Local development workflow

1. `conda activate ais-tree`
2. `streamlit run app.py` — opens `localhost:8501`, auto-reloads on file save.
3. Local secrets in `.streamlit/secrets.toml` (gitignored). Same format as Cloud secrets.
4. Test against the real sheet — there is no staging sheet. Acceptance writes are gated behind the human Accept button, so this is safe as long as that gate is respected.

## Deployment

- Streamlit Community Cloud, deploying from `main` branch of the public GitHub repo.
- Secrets (in Streamlit Cloud dashboard, TOML format — same content as local `.streamlit/secrets.toml`):
  - `[gcp_service_account]` — the full service account JSON converted to TOML. `private_key` must preserve `\n` as literal `\n` in the TOML string.
  - `ANTHROPIC_API_KEY`
  - `SHEET_ID`
  - `APP_PASSWORD`
- `requirements.txt` must list: `streamlit`, `gspread`, `trafilatura`, `anthropic`, `requests`.
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

### ARENA (aisdb_008)
ARENA moved from `bluedot.org/arena` (404) to `bluedot.org/courses/arena`. URL corrected in sheet. Also has its own standalone site at `arena.education` — either URL works, the BlueDot one is preferred for consistency with the other BlueDot entries. Note: ARENA also has JS-rendered content like the other BlueDot courses (same Next.js stack), so the same `__NEXT_DATA__` extraction caveat applies for deadlines.

### OpenAI Residency (aisdb_018)
Produced a fetch_error in the first live test run. The URL (`openai.com/residency/`) returns 200 and trafilatura extracts content correctly in isolation — likely a transient network or rate-limit issue. If fetch errors persist, the page may be behind a bot-detection layer and would need a Playwright fallback (out of scope for v1).

### BlueDot (bluedot.org)

Deadline and cohort start dates are visible on the page but are **not extracted by trafilatura**. The dates are embedded in a Next.js JSON payload (`__NEXT_DATA__`) and rendered client-side — trafilatura only sees the static HTML, which contains the label "Schedule" but not the actual dates.

The data is available in the raw HTML: the `soonestDeadline` field appears in a `<script id="__NEXT_DATA__">` JSON blob, and "Apply by DD Mon" appears in button text. A workaround is to parse `__NEXT_DATA__` directly from the raw response rather than relying on trafilatura's extraction. This is not implemented in v1 — for now, BlueDot deadlines must be checked and updated manually.

Affects: `aisdb_001`, `aisdb_002`, `aisdb_003` (all bluedot.org/courses/* URLs).

## Open questions to resolve in implementation

- Whether to add a "needs follow-up" state distinct from rejected (cases where the LLM correctly detected a change but the proposed values are wrong).
- How aggressively to normalize date formats — "Dec 15, 2025" vs "2025-12-15" should be considered equal. Implement explicit normalizer in `diff.py`.
- Whether to log every LLM response (full request + response) to a `_audit` tab for debugging early on. Default: yes for the first few weeks, behind a config flag.

## Known issues / backlog

### 1. Current date missing from LLM prompt
The LLM has no awareness of today's date, so it cannot correctly infer whether an application deadline is in the past or future. E.g. Pivotal had a deadline of 3 May — the LLM proposed `application_status: open` even though it had already passed. Fix: inject `today: YYYY-MM-DD` into the user prompt so the LLM can reason about open/closed correctly.

### 2. `recompensation` field should be numeric (USD)
The field currently accepts free text but should store a single number in USD (e.g. `8400` for $8,400/month, `0` for none). Complex recompensation structures (lotteries, variable stipends, housing credits) should be summarised as the minimum guaranteed value in `recompensation` and the full detail noted in `notes`. Conversion from other currencies should happen at write time. A broader task: define expected types for all fields in `config.json` and add a validation pass in `diff.py` or `pipeline.py` that warns when a proposed value doesn't match the expected type.

### 3. Frontier AI Governance has two separate programs
`aisdb_003` (BlueDot Frontier AI Governance) covers both an intensive 5-day track and a part-time 5-week track. These should probably be two separate sheet entries. Needs a manual data decision before implementation.

### 4. Two-phase run: diff first, then confirm before LLM
Currently "Run updater" fetches all pages, runs diffs, and calls the LLM in one pass. Better UX: split into two steps. Phase 1 (cheap): fetch all pages, compute hashes, identify which entries changed — show a summary table (N changed, N unchanged, N errors) and an estimated token cost (assume ~3¢ per changed entry as a rough guide). Phase 2 (expensive): user confirms, then LLM calls are made only for changed entries. This avoids surprise API costs and lets the user abort if something looks wrong.