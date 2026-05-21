#!/usr/bin/env python3
"""
One-time migration: convert duration (→ weeks_int) and time_commitment (→ hrs_per_week_int).

Ranges are converted to their maximum value and flagged with
trigger_check_next_update=true in _updater_state so the next updater run
will ask the LLM to read the page and supply a precise value.

Run from the project root:
    conda run -n ais-tree python scripts/migrate_numeric_fields.py
"""
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from updater.sheets import open_sheet, STATE_TAB

# value → (converted_value, needs_trigger)
DURATION_MAP = {
    "1-3 months":   ("12",  True),
    "1-4 weeks":    ("4",   True),
    "10 weeks":     ("10",  False),
    "12 weeks":     ("12",  False),
    "2 months":     ("8",   False),
    "3-4 months":   ("16",  True),
    "3-6 months":   ("24",  True),
    "5 months":     ("20",  False),
    "6 days (intensive) or 6 weeks (part-time)": ("6", True),
    "6-12 months":  ("48",  True),
    "Ongoing/variable": ("[unclear]", False),
}

TIME_MAP = {
    "<5 hrs/week":  ("5",   True),
    "~5 hrs/week":  ("5",   False),
    "2 hrs/week":   ("2",   False),
    "5-10 hrs/week": ("10", True),
    "10 hrs/week":  ("10",  False),
    "10-20 hrs/week": ("20", True),
    "Full-time":    ("40",  False),
    "~5 hrs/day (intensive) or ~5 hrs/week (part-time)": ("25", True),
}


def main() -> None:
    secrets_path = Path(__file__).parent.parent / ".streamlit" / "secrets.toml"
    with open(secrets_path, "rb") as f:
        secrets = tomllib.load(f)

    sa = dict(secrets["gcp_service_account"])
    if "\\n" in sa.get("private_key", ""):
        sa["private_key"] = sa["private_key"].replace("\\n", "\n")

    spreadsheet = open_sheet(sa, secrets["SHEET_ID"])
    ws = spreadsheet.sheet1
    headers = ws.row_values(1)
    all_rows = ws.get_all_values()
    col = {h: i + 1 for i, h in enumerate(headers)}

    trigger_ids: set[str] = set()
    main_changes: list[str] = []

    for row_idx, row_vals in enumerate(all_rows[1:], start=2):
        row = dict(zip(headers, row_vals))
        fid = row.get("ID", "")

        duration_val = row.get("duration", "")
        if duration_val in DURATION_MAP:
            new_val, flag = DURATION_MAP[duration_val]
            ws.update_cell(row_idx, col["duration"], new_val)
            main_changes.append(f"  {fid}: duration: {duration_val!r} → {new_val!r}" + (" [trigger]" if flag else ""))
            if flag:
                trigger_ids.add(fid)

        time_val = row.get("time_commitment", "")
        if time_val in TIME_MAP:
            new_val, flag = TIME_MAP[time_val]
            ws.update_cell(row_idx, col["time_commitment"], new_val)
            main_changes.append(f"  {fid}: time_commitment: {time_val!r} → {new_val!r}" + (" [trigger]" if flag else ""))
            if flag:
                trigger_ids.add(fid)

    print("Main sheet changes:")
    for c in main_changes:
        print(c)
    if not main_changes:
        print("  (none)")

    # Update _updater_state trigger flags
    titles = [sh.title for sh in spreadsheet.worksheets()]
    if STATE_TAB not in titles:
        print(f"\n{STATE_TAB} tab not yet created — trigger flags will be set on first updater run.")
        return

    state_ws = spreadsheet.worksheet(STATE_TAB)
    state_headers = state_ws.row_values(1)

    if "trigger_check_next_update" not in state_headers:
        # Resize to make room, then add the column header
        next_col = len(state_headers) + 1
        state_ws.resize(rows=500, cols=next_col)
        state_ws.update_cell(1, next_col, "trigger_check_next_update")
        state_headers.append("trigger_check_next_update")
        print(f"\nAdded trigger_check_next_update column at position {next_col}.")

    trigger_col = state_headers.index("trigger_check_next_update") + 1
    id_col = state_headers.index("id")
    state_rows = state_ws.get_all_values()[1:]  # skip header

    state_changes: list[str] = []
    for i, row in enumerate(state_rows, start=2):
        fid = row[id_col] if id_col < len(row) else ""
        if fid in trigger_ids:
            state_ws.update_cell(i, trigger_col, "true")
            state_changes.append(f"  {fid}: trigger_check_next_update = true")

    unfound = trigger_ids - {r[id_col] for r in state_rows if id_col < len(r)}
    if unfound:
        print(f"\nNote: {sorted(unfound)} not yet in {STATE_TAB} — trigger will apply on first run when they appear.")

    print("\nState tab trigger flags set:")
    for c in state_changes:
        print(c)
    if not state_changes:
        print("  (none — rows may not be in state tab yet)")

    print("\nMigration complete.")


if __name__ == "__main__":
    main()
