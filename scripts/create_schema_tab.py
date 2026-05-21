"""One-time script: create a _schema tab on the sheet with field documentation."""
import sys
import tomllib
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

TAB_NAME = "_schema"

ROWS = [
    # (name, description, expected_value_type)
    ("ID",                          "Stable unique identifier for this fellowship",                           "Text (e.g. aisdb_001) — never changes"),
    ("url",                         "Canonical URL of the fellowship page",                                   "URL — never changes"),
    ("name",                        "Full name of the fellowship or program",                                 "Free text"),
    ("organization",                "Name of the organizing institution or group",                            "Free text"),
    ("track",                       "Focus area of the program",                                              "One of: technical, governance, general, neutral"),
    ("program_type",                "Type of program",                                                        "One of: fellowship, course, workshop, mentorship, bootcamp, advising"),
    ("format",                      "Delivery format",                                                        "One of: online, in-person, hybrid"),
    ("pacing",                      "Pacing structure (rolling admissions is still cohort-based)",            "One of: self-paced, cohort-based"),
    ("geographic_focus",            "Geographic scope of the program",                                        "One of: global, us-centric, uk/eu-centric"),
    ("for_student",                 "Suitable for students (undergrad / grad)",                               "0 or 1"),
    ("for_early_career",            "Suitable for early-career professionals (0–5 years experience)",         "0 or 1"),
    ("for_mid_career",              "Suitable for mid-career professionals",                                  "0 or 1"),
    ("for_senior",                  "Suitable for senior professionals or researchers",                       "0 or 1"),
    ("for_career_switch",           "Designed to help people transition into AI safety",                      "0 or 1"),
    ("description",                 "Short description of the program's goals and content",                   "Free text"),
    ("duration",                    "How long the program runs",                                              "Integer weeks (e.g. 10); <1 for sub-week programs; [unclear] if not stated"),
    ("time_commitment",             "Expected weekly effort",                                                 "Integer hours/week (e.g. 10); 40 = full-time; [unclear] if not stated"),
    ("cost",                        "Cost to participate",                                                    "Free text (e.g. Free, $500, Varies); [unclear] if not stated"),
    ("recompensation",              "Minimum guaranteed compensation paid to participants",                   "$ as plain integer, no symbols (e.g. 8400); 0 = none; [unclear] if not stated"),
    ("prerequisites",               "Required background or skills",                                          "Free text (e.g. Python, ML basics); [unclear] if not stated"),
    ("tags",                        "Topic tags for filtering",                                               "Comma-separated lowercase words (e.g. alignment, interpretability, policy)"),
    ("application_status",          "Current application status",                                             "One of: open, closed, rolling, [unclear]"),
    ("next_deadline",               "Next application deadline",                                              "YYYY-MM-DD date; [unclear] if not stated"),
    ("next_cohort_start",           "Start date of the next cohort",                                          "YYYY-MM-DD date; [unclear] if not stated"),
    ("last_verified",               "Date this row was last processed by the updater",                        "YYYY-MM-DD date — auto-managed, do not edit"),
    ("notes",                       "Free-text notes for human reviewers only",                               "Free text — never modified by the updater"),
    ("notes_for_claude_during_update", "Hints passed to the LLM when proposing updates for this row",        "Free text — never modified by the updater"),
]


def load_secrets():
    p = Path(__file__).parent.parent / ".streamlit" / "secrets.toml"
    with open(p, "rb") as f:
        return tomllib.load(f)


def main():
    secrets = load_secrets()
    sa = dict(secrets["gcp_service_account"])
    if "\\n" in sa.get("private_key", ""):
        sa["private_key"] = sa["private_key"].replace("\\n", "\n")

    creds = Credentials.from_service_account_info(sa, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(secrets["SHEET_ID"])

    # Delete existing tab if present so script is re-runnable
    titles = [ws.title for ws in spreadsheet.worksheets()]
    if TAB_NAME in titles:
        spreadsheet.del_worksheet(spreadsheet.worksheet(TAB_NAME))
        print(f"Deleted existing {TAB_NAME} tab.")

    ws = spreadsheet.add_worksheet(title=TAB_NAME, rows=len(ROWS) + 2, cols=3)

    header = [["field name", "description", "expected value / format"]]
    ws.update("A1", header)
    ws.update("A2", ROWS)

    print(f"Created {TAB_NAME} tab with {len(ROWS)} field rows.")


if __name__ == "__main__":
    main()
