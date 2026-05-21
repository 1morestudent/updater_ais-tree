#!/usr/bin/env python3
"""
One-time admin script: split aisdb_003 (Frontier AI Governance) into two sheet rows.

BlueDot's Frontier AI Governance page covers two distinct programs:
  - Intensive:  5-day in-person track
  - Part-time:  5-week online track

Run from the project root:
    conda run -n ais-tree python scripts/split_aisdb003.py

After running, open the sheet and manually update the field values that differ
between the two tracks (name, duration, time_commitment, format, pacing, url if
different, deadlines, etc.). Both rows start as copies of the original.
"""
import json
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from updater.sheets import open_sheet


def main() -> None:
    secrets_path = Path(__file__).parent.parent / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        print(f"ERROR: secrets file not found at {secrets_path}")
        sys.exit(1)

    with open(secrets_path, "rb") as f:
        secrets = tomllib.load(f)

    sa = dict(secrets["gcp_service_account"])
    if "\\n" in sa.get("private_key", ""):
        sa["private_key"] = sa["private_key"].replace("\\n", "\n")

    spreadsheet = open_sheet(sa, secrets["SHEET_ID"])
    ws = spreadsheet.sheet1
    headers = ws.row_values(1)

    if "ID" not in headers:
        print("ERROR: no ID column found in sheet")
        sys.exit(1)

    id_col = headers.index("ID") + 1  # 1-based
    all_ids = ws.col_values(id_col)

    if "aisdb_003b" in all_ids:
        print("aisdb_003b already exists in the sheet — aborting to avoid duplicates.")
        sys.exit(1)

    if "aisdb_003" not in all_ids:
        print("aisdb_003 not found in sheet — nothing to split.")
        sys.exit(1)

    row_idx = all_ids.index("aisdb_003") + 1  # 1-based
    original_values = ws.row_values(row_idx)

    # Pad to match header length
    while len(original_values) < len(headers):
        original_values.append("")

    field_map = dict(zip(headers, original_values))
    print("Current aisdb_003:")
    print(json.dumps(field_map, indent=2))
    print()
    print("This will:")
    print("  1. Rename aisdb_003 → 'Frontier AI Governance (Intensive)' + add a split note")
    print("  2. Append aisdb_003b as a copy → 'Frontier AI Governance (Part-time)' + add a split note")
    print()
    print("After this script, update both rows in the sheet with correct track-specific values.")
    print()

    confirm = input("Proceed? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    name_col = headers.index("name") + 1
    notes_col = headers.index("notes") + 1 if "notes" in headers else None

    # Update aisdb_003 in-place
    ws.update_cell(row_idx, name_col, "Frontier AI Governance (Intensive)")
    if notes_col:
        ws.update_cell(
            row_idx,
            notes_col,
            "SPLIT 2025: intensive 5-day track. See aisdb_003b for part-time. "
            "Update duration, time_commitment, format, pacing, and deadlines to match this track.",
        )

    # Build new row for aisdb_003b
    new_row = list(original_values)
    new_row[headers.index("ID")] = "aisdb_003b"
    new_row[headers.index("name")] = "Frontier AI Governance (Part-time)"
    if "notes" in headers:
        new_row[headers.index("notes")] = (
            "SPLIT 2025: part-time 5-week track. See aisdb_003 for intensive. "
            "Update duration, time_commitment, format, pacing, and deadlines to match this track."
        )

    ws.append_row(new_row, value_input_option="USER_ENTERED")

    print()
    print("Done. Next steps:")
    print("  • aisdb_003 (Intensive): set duration='5 days', update time_commitment, format, pacing")
    print("  • aisdb_003b (Part-time): set duration='5 weeks', update time_commitment, format, pacing")
    print("  • Verify/update next_deadline and next_cohort_start for each track if they differ")
    print("  • Update notes_for_claude_during_update for both rows if needed")


if __name__ == "__main__":
    main()
