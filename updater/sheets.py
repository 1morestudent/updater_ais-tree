import json
from datetime import date
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

STATE_TAB = "_updater_state"
PROPOSALS_TAB = "_proposals"

PROPOSALS_COLUMNS = [
    "run_at", "id", "name", "url", "classification", "reasoning",
    "triggered", "field", "current_value", "proposed_value",
    "source_snippet", "type_warning", "status",
]

STATE_COLUMNS = [
    "id",
    "url",
    "last_hash",
    "last_checked_at",
    "last_classification",
    "last_result",
    "trigger_check_next_update",
]


def _build_client(service_account_info: dict) -> gspread.Client:
    creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    return gspread.authorize(creds)


def open_sheet(service_account_info: dict, sheet_id: str) -> gspread.Spreadsheet:
    client = _build_client(service_account_info)
    return client.open_by_key(sheet_id)


def read_main_rows(spreadsheet: gspread.Spreadsheet) -> list[dict]:
    ws = spreadsheet.sheet1
    return ws.get_all_records(default_blank="")


def ensure_state_tab(spreadsheet: gspread.Spreadsheet) -> gspread.Worksheet:
    titles = [ws.title for ws in spreadsheet.worksheets()]
    if STATE_TAB not in titles:
        ws = spreadsheet.add_worksheet(title=STATE_TAB, rows=500, cols=len(STATE_COLUMNS))
        ws.append_row(STATE_COLUMNS)
        return ws
    ws = spreadsheet.worksheet(STATE_TAB)
    # Backfill any columns added after initial creation
    existing = ws.row_values(1)
    for col_name in STATE_COLUMNS:
        if col_name not in existing:
            ws.update_cell(1, len(existing) + 1, col_name)
            existing.append(col_name)
    return ws


def read_state(state_ws: gspread.Worksheet) -> dict[str, dict]:
    rows = state_ws.get_all_records(default_blank="")
    return {row["id"]: row for row in rows}


def upsert_state_row(state_ws: gspread.Worksheet, state: dict[str, dict], row: dict[str, Any]) -> None:
    fellowship_id = row["id"]
    all_rows = state_ws.get_all_values()
    headers = all_rows[0] if all_rows else STATE_COLUMNS
    values = [str(row.get(col, "")) for col in headers]

    if fellowship_id in state:
        # find the row index (1-based, +1 for header)
        row_indices = [i + 2 for i, r in enumerate(all_rows[1:]) if r and r[0] == fellowship_id]
        if row_indices:
            state_ws.update(f"A{row_indices[0]}", [values])
            return
    state_ws.append_row(values)


def find_sheet_row(spreadsheet: gspread.Spreadsheet, fellowship_id: str) -> int | None:
    """Return 1-based sheet row number for the given fellowship ID, or None if not found."""
    ws = spreadsheet.sheet1
    all_values = ws.get_all_values()
    if not all_values:
        return None
    id_col = all_values[0].index("ID") if "ID" in all_values[0] else 0
    for i, row in enumerate(all_values[1:], start=2):
        if row[id_col] == fellowship_id:
            return i
    return None


def accept_fellowship(spreadsheet: gspread.Spreadsheet, fellowship_id: str, updates: dict[str, Any]) -> bool:
    """Write accepted field values + last_verified to the main sheet. Returns True on success."""
    sheet_row = find_sheet_row(spreadsheet, fellowship_id)
    if sheet_row is None:
        return False
    ws = spreadsheet.sheet1
    headers = ws.row_values(1)
    updates_with_verified = {**updates, "last_verified": date.today().isoformat()}
    for field, value in updates_with_verified.items():
        if field in headers:
            col = headers.index(field) + 1
            ws.update_cell(sheet_row, col, value)
    return True


def set_last_verified_by_id(spreadsheet: gspread.Spreadsheet, fellowship_id: str) -> None:
    sheet_row = find_sheet_row(spreadsheet, fellowship_id)
    if sheet_row is None:
        return
    ws = spreadsheet.sheet1
    headers = ws.row_values(1)
    if "last_verified" in headers:
        col = headers.index("last_verified") + 1
        ws.update_cell(sheet_row, col, date.today().isoformat())


def set_trigger_flag(state_ws: gspread.Worksheet, fellowship_id: str, value: str) -> None:
    """Set trigger_check_next_update for a row in the state tab."""
    all_values = state_ws.get_all_values()
    if not all_values:
        return
    headers = all_values[0]
    if "trigger_check_next_update" not in headers or "id" not in headers:
        return
    id_col = headers.index("id")
    trigger_col = headers.index("trigger_check_next_update") + 1
    for i, row in enumerate(all_values[1:], start=2):
        if row[id_col] == fellowship_id:
            state_ws.update_cell(i, trigger_col, value)
            return


def update_state_result(state_ws: gspread.Worksheet, fellowship_id: str, last_result: str) -> None:
    """Update only the last_result field in the state tab for a given fellowship."""
    all_values = state_ws.get_all_values()
    if not all_values:
        return
    headers = all_values[0]
    if "last_result" not in headers or "id" not in headers:
        return
    id_col = headers.index("id")
    result_col = headers.index("last_result") + 1  # 1-based for update_cell
    for i, row in enumerate(all_values[1:], start=2):
        if row[id_col] == fellowship_id:
            state_ws.update_cell(i, result_col, last_result)
            return


# ---------------------------------------------------------------------------
# Proposals tab (_proposals)
# ---------------------------------------------------------------------------

def ensure_proposals_tab(spreadsheet: gspread.Spreadsheet) -> gspread.Worksheet:
    titles = [ws.title for ws in spreadsheet.worksheets()]
    if PROPOSALS_TAB not in titles:
        ws = spreadsheet.add_worksheet(title=PROPOSALS_TAB, rows=2000, cols=len(PROPOSALS_COLUMNS))
        ws.append_row(PROPOSALS_COLUMNS)
        return ws
    return spreadsheet.worksheet(PROPOSALS_TAB)


def supersede_pending_proposals(ws: gspread.Worksheet) -> None:
    """Mark all pending rows as superseded when a new run starts."""
    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        return
    headers = all_rows[0]
    if "status" not in headers:
        return
    status_idx = headers.index("status")
    status_col = chr(ord('A') + status_idx)
    updates = [
        {"range": f"{status_col}{i}", "values": [["superseded"]]}
        for i, row in enumerate(all_rows[1:], start=2)
        if len(row) > status_idx and row[status_idx] == "pending"
    ]
    if updates:
        ws.batch_update(updates)


def write_proposal_rows(ws: gspread.Worksheet, result: dict) -> None:
    """Append one row per proposed field change for a single result."""
    if result.get("status") != "pending":
        return
    base = [
        result.get("checked_at", ""),
        result["id"],
        result.get("name", ""),
        result.get("url", ""),
        result.get("classification", ""),
        result.get("reasoning", ""),
        str(result.get("triggered", False)).lower(),
    ]
    type_warnings = {w["field"]: w["warning"] for w in result.get("type_warnings", [])}
    changes = result.get("changes", [])
    if not changes:
        ws.append_row(base + ["[none]", "", "", "", "", "pending"])
        return
    rows = [
        base + [
            change["field"],
            change.get("old", ""),
            change.get("proposed", ""),
            result.get("snippets", {}).get(change["field"], ""),
            type_warnings.get(change["field"], ""),
            "pending",
        ]
        for change in changes
    ]
    ws.append_rows(rows)


def read_pending_proposals(ws: gspread.Worksheet) -> list[dict]:
    """Reconstruct a results list from pending rows, grouped by fellowship id."""
    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        return []
    headers = all_rows[0]

    def get(row, name):
        idx = headers.index(name) if name in headers else -1
        return row[idx] if 0 <= idx < len(row) else ""

    by_id: dict[str, dict] = {}
    id_order: list[str] = []
    for row in all_rows[1:]:
        if get(row, "status") != "pending":
            continue
        fid = get(row, "id")
        if not fid:
            continue
        if fid not in by_id:
            id_order.append(fid)
            by_id[fid] = {
                "id": fid,
                "name": get(row, "name"),
                "url": get(row, "url"),
                "status": "pending",
                "classification": get(row, "classification"),
                "reasoning": get(row, "reasoning"),
                "triggered": get(row, "triggered") == "true",
                "changes": [],
                "snippets": {},
                "type_warnings": [],
                "checked_at": get(row, "run_at"),
                "new_hash": None,
                "error": None,
                "llm_raw": None,
            }
        field = get(row, "field")
        if field and field != "[none]":
            by_id[fid]["changes"].append({
                "field": field,
                "old": get(row, "current_value"),
                "proposed": get(row, "proposed_value"),
            })
            snippet = get(row, "source_snippet")
            if snippet:
                by_id[fid]["snippets"][field] = snippet
            warning = get(row, "type_warning")
            if warning:
                by_id[fid]["type_warnings"].append({"field": field, "warning": warning})

    return [by_id[fid] for fid in id_order]


def update_proposal_status(ws: gspread.Worksheet, fellowship_id: str, new_status: str) -> None:
    """Update status for all pending rows matching fellowship_id."""
    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        return
    headers = all_rows[0]
    if "id" not in headers or "status" not in headers:
        return
    id_idx = headers.index("id")
    status_idx = headers.index("status")
    status_col = chr(ord('A') + status_idx)
    updates = [
        {"range": f"{status_col}{i}", "values": [[new_status]]}
        for i, row in enumerate(all_rows[1:], start=2)
        if (len(row) > max(id_idx, status_idx)
            and row[id_idx] == fellowship_id
            and row[status_idx] == "pending")
    ]
    if updates:
        ws.batch_update(updates)
