# AIS-tree Fellowship Updater

A Streamlit tool that keeps the [AIS-tree](https://ais.tree) fellowship directory up to date. It detects when fellowship websites change, asks Claude to propose field-level updates, and lets a human reviewer accept or reject each proposal before anything is written back to the sheet.

The data lives in a Google Sheet published as a live CSV, read directly by the AIS-tree frontend. Every accepted change goes live within ~5 minutes.

---

## How it works

The pipeline has two phases, separated by a human confirmation step:

**Phase 1 — Fetch (cheap, always runs first)**
The app fetches every fellowship URL, extracts the main text with `trafilatura`, and computes a hash. It compares that hash against the stored hash in the `_updater_state` sheet tab. If the hash is unchanged, the row is marked as verified and no LLM call is made. If the hash changed (or a manual re-check was triggered), the row is queued for phase 2.

**Phase 2 — LLM analysis (confirmed by you)**
After phase 1, the app shows a summary — how many rows changed, how many were unchanged, estimated API cost (~$0.03/entry). You click "Analyse N entries" to proceed. For each changed row, Claude receives the current field values, the extracted page text, and the full field schema, and returns proposed new values plus a classification:

- **Highly relevant** — change to a deadline, application status, or cohort start; or a previously `[unclear]` field filled in; or a material change to cost/prerequisites/duration.
- **Potentially relevant** — ambiguous change, partial info, or a rewording of a semi-stable field.
- **Not relevant** — page redesign or copy edits with no underlying factual change.

**Review**
Results are grouped by classification. For each fellowship card you can:
- **Accept** — writes the proposed values to the main sheet immediately.
- **Reject** — dismisses the proposals.
- **Edit then accept** — opens an inline form to modify any proposed value before writing.

If you close the tab mid-review, the proposals are persisted in the `_proposals` sheet tab. On next load you'll see a "Resume pending review (N)" button.

---

## Sheet structure

The tool manages three tabs internally. Only `sheet1` feeds the public CSV.

| Tab | Purpose |
|---|---|
| `sheet1` | Main fellowship data — the source of truth, feeds the live public CSV |
| `_updater_state` | Per-row hash, last-checked timestamp, classification, result |
| `_proposals` | Audit log of every LLM proposal and its outcome (accepted/rejected/superseded) |
| `_schema` | Human-readable field documentation (name, description, expected format) |

---

## Field schema

The full schema is documented in the `_schema` tab of the sheet and versioned in `config.json`. Key types:

| Type | Example fields | Format |
|---|---|---|
| Free text | `name`, `description`, `prerequisites` | Plain string |
| Enum | `track`, `format`, `application_status` | Fixed allowed values — see `config.json` |
| Binary flag | `for_student`, `for_early_career`, … | `0` or `1` |
| Date | `next_deadline`, `next_cohort_start` | `YYYY-MM-DD` or `[unclear]` |
| Integer weeks | `duration` | Whole number (e.g. `10`); `<1` for sub-week; `[unclear]` |
| Hours/week | `time_commitment` | Whole number (e.g. `10`; `40` = full-time); `[unclear]` |
| USD integer | `recompensation` | Plain integer, no symbols (e.g. `8400`); `0` = none; `[unclear]` |
| Comma-separated | `tags` | Lowercase, sorted (e.g. `alignment, interpretability`) |

Any field may hold `[unclear]` — filling it in counts as a meaningful change and will be flagged as highly relevant.

---

## Adding a new fellowship

1. Add a row to `sheet1` with a unique `ID` (format: `aisdb_NNN` or `aisdb_NNNa/b` for variants) and a `url`.
2. Fill in as many fields as you know.
3. Run the updater — the new row has no stored hash, so it will always be treated as changed and go through LLM analysis on the next run.

A duplicate-ID check runs at the start of every run and blocks execution if any IDs are repeated.

---

## Forcing a re-check

If you want the LLM to re-analyse a row even though its page content hasn't changed (e.g. you suspect stale data), set `trigger_check_next_update` to `true` in the `_updater_state` tab for that row. The next run will force it through the LLM and clear the flag. The review card will show *(forced re-check)* to indicate this.

---

## Running locally

### Prerequisites

- Python 3.11 via conda
- A Google service account with editor access to the sheet
- An Anthropic API key

### Setup

```bash
git clone https://github.com/1morestudent/updater_ais-tree.git
cd updater_ais-tree

conda create -n ais-tree python=3.11
conda activate ais-tree
pip install -r requirements.txt
```

Create `.streamlit/secrets.toml` (this file is gitignored — never commit it):

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
SHEET_ID = "your-google-sheet-id"
APP_PASSWORD = "..."

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
```

> The app password is shared separately with authorised users — contact the project maintainer.

> **TOML order matters:** `ANTHROPIC_API_KEY`, `SHEET_ID`, and `APP_PASSWORD` must appear **before** the `[gcp_service_account]` section header, or they will be absorbed into it.

### Start the server

```bash
streamlit run app.py
```

Or headless on a specific port:

```bash
streamlit run app.py --server.port 8501 --server.headless true
```

Opens at `http://localhost:8501`. The app reloads automatically on file save.

---

## Deployment

The app is deployed on [Streamlit Community Cloud](https://streamlit.io/cloud) from the `main` branch. Pushing to `main` triggers an automatic redeploy in ~60 seconds.

Secrets are configured in the Streamlit Cloud dashboard under the app settings, using the same TOML format as `.streamlit/secrets.toml` above. The `private_key` value must keep literal `\n` sequences (not real newlines) in the dashboard.

---

## Repository layout

```
updater_ais-tree/
├── app.py                  # Streamlit entry point and UI
├── updater/
│   ├── pipeline.py         # fetch_row (phase 1) + process_changed_row (phase 2)
│   ├── sheets.py           # gspread wrapper — all sheet reads and writes
│   ├── llm.py              # Anthropic API call and response parsing
│   └── diff.py             # Field-level normalization, comparison, type validation
├── prompts/
│   └── classify.md         # System prompt for the LLM — edit here, no redeploy needed
├── scripts/
│   ├── create_schema_tab.py       # Creates the _schema documentation tab on the sheet
│   └── migrate_numeric_fields.py  # One-time migration (already run)
├── config.json             # Field schema, allowed values, model name, fetch settings
└── requirements.txt
```

---

## Known limitations

- **BlueDot pages (aisdb_001–003):** Deadlines are rendered client-side via Next.js and are not extracted by `trafilatura`. The LLM is warned via the `notes_for_claude_during_update` field, but deadline changes on these pages must be verified manually.
- **JS-heavy pages generally:** `requests` + `trafilatura` only sees server-rendered HTML. A Playwright fallback is on the backlog but not yet implemented.
- **No auto-accept:** Every proposed change requires a human click, even obviously correct updates. This is intentional for v1.
