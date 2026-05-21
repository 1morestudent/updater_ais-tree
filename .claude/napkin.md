# AIS-tree Updater — Napkin

## Start the server
```bash
# conda is NOT on PATH in non-login shells — always use full path:
/home/claude_usr/miniconda3/envs/ais-tree/bin/streamlit run app.py --server.port 8501 --server.headless true
```
App password: in `.streamlit/secrets.toml` → `APP_PASSWORD`

## Sheets quota — critical rule
60 reads/minute. Never add per-row read calls inside loops. Always:
- Read state once → cache row numbers in the dict (`_row_num` key in `read_state` output)
- Batch `last_verified` writes with `batch_set_last_verified()` after the loop
- Use `batch_update()` for multi-cell writes in `_proposals` and `_updater_state`

## Sheet tabs
| Tab | Purpose |
|---|---|
| sheet1 | Main fellowship data — feeds live public CSV. Writes go live in 5–10 min. |
| `_updater_state` | Hash + classification per row. `trigger_check_next_update="true"` forces LLM re-check. |
| `_proposals` | One row per proposed field change. `status`: pending/accepted/rejected/superseded. Resume source. |
| `_schema` | Human-readable field docs. Managed by `scripts/create_schema_tab.py`, not the pipeline. |

## Adding new fellowships
Add a row to sheet1 with a unique `ID` and `url`. Next run picks it up automatically (no prior hash → treated as changed → goes through LLM). Duplicate-ID check runs at startup.

## Diff table rendering
Uses `st.table(pd.DataFrame(table_data).T)` — wraps text naturally. Do NOT use `st.dataframe` with `TextColumn(wrap=True)` — that param doesn't exist in this Streamlit build.

## Non-negotiables
- Never auto-write to main sheet — always human Accept
- Never commit `.streamlit/secrets.toml` or `service_account.json`
- Never touch system Python — conda env `ais-tree` only
- No `sudo` — print needed commands and wait

## Description tone
The LLM prompt instructs the model to write `description` neutrally. Promotional/subjective claims must be attributed to the program (e.g. "described by the program as ideal for newcomers"), not stated as fact.
