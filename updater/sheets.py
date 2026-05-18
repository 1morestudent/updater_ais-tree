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
STATE_COLUMNS = [
    "id",
    "url",
    "last_hash",
    "last_checked_at",
    "last_classification",
    "last_result",
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
    return spreadsheet.worksheet(STATE_TAB)


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
