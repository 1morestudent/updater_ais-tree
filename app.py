import json

import anthropic
import streamlit as st

from updater.llm import build_schema_description, load_system_prompt
from updater.pipeline import process_row
from updater.sheets import (
    accept_fellowship,
    ensure_state_tab,
    open_sheet,
    read_main_rows,
    read_state,
    set_last_verified_by_id,
    update_state_result,
    upsert_state_row,
)

st.set_page_config(page_title="AIS-tree Updater", layout="wide")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@st.cache_resource
def load_config() -> dict:
    with open("config.json") as f:
        return json.load(f)


config = load_config()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("AIS-tree Updater")
    pw = st.text_input("Password", type="password")
    if st.button("Login"):
        if pw == st.secrets["APP_PASSWORD"]:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Wrong password")
    st.stop()


# ---------------------------------------------------------------------------
# Cached resources
# ---------------------------------------------------------------------------

@st.cache_resource
def get_spreadsheet():
    sa = dict(st.secrets["gcp_service_account"])
    # gspread needs private_key newlines unescaped
    if "\\n" in sa.get("private_key", ""):
        sa["private_key"] = sa["private_key"].replace("\\n", "\n")
    return open_sheet(sa, st.secrets["SHEET_ID"])


@st.cache_resource
def get_llm_client():
    return anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

if "results" not in st.session_state:
    st.session_state.results = []
if "review" not in st.session_state:
    st.session_state.review = {}  # id -> {"action": "accepted"|"rejected"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BADGE = {
    "highly_relevant": "🔴 Highly relevant",
    "potentially_relevant": "🟡 Potentially relevant",
    "not_relevant": "🟢 Not relevant",
}

STATUS_ORDER = ["highly_relevant", "potentially_relevant", "not_relevant"]


def _do_accept(result: dict, edits: dict | None = None) -> None:
    updates = {c["field"]: (edits or {}).get(c["field"], c["proposed"]) for c in result["changes"]}
    spreadsheet = get_spreadsheet()
    state_ws = ensure_state_tab(spreadsheet)
    accept_fellowship(spreadsheet, result["id"], updates)
    update_state_result(state_ws, result["id"], "accepted")
    st.session_state.review[result["id"]] = {"action": "accepted"}


def _do_reject(result: dict) -> None:
    spreadsheet = get_spreadsheet()
    state_ws = ensure_state_tab(spreadsheet)
    update_state_result(state_ws, result["id"], "rejected")
    st.session_state.review[result["id"]] = {"action": "rejected"}


def _render_card(result: dict) -> None:
    fid = result["id"]
    review = st.session_state.review.get(fid)

    with st.container(border=True):
        col_name, col_badge = st.columns([4, 1])
        with col_name:
            st.markdown(f"**[{result['name']}]({result['url']})**  `{fid}`")
        with col_badge:
            st.markdown(BADGE.get(result["classification"], ""))

        if result.get("reasoning"):
            st.caption(result["reasoning"])

        if review:
            action = review["action"]
            if action == "accepted":
                st.success("Accepted")
            else:
                st.warning("Rejected")
            return

        if not result["changes"]:
            st.info("No field-level changes detected (hash changed, but diff found nothing).")
            if st.button("Reject", key=f"reject_nochange_{fid}"):
                _do_reject(result)
                st.rerun()
            return

        editing = st.session_state.get(f"editing_{fid}", False)

        if not editing:
            # Diff table
            table_data = [
                {
                    "Field": c["field"],
                    "Current": c["old"],
                    "Proposed": c["proposed"],
                    "Source snippet": result["snippets"].get(c["field"], "")[:120],
                }
                for c in result["changes"]
            ]
            st.dataframe(table_data, use_container_width=True, hide_index=True)

            col1, col2, col3 = st.columns(3)
            with col1:
                if st.button("Accept", key=f"accept_{fid}", type="primary"):
                    _do_accept(result)
                    st.rerun()
            with col2:
                if st.button("Reject", key=f"reject_{fid}"):
                    _do_reject(result)
                    st.rerun()
            with col3:
                if st.button("Edit then accept", key=f"edit_{fid}"):
                    st.session_state[f"editing_{fid}"] = True
                    st.rerun()

        else:
            # Edit mode — use a form so inputs don't trigger per-keystroke reruns
            with st.form(key=f"form_{fid}"):
                edits = {}
                for c in result["changes"]:
                    snippet = result["snippets"].get(c["field"], "")
                    if snippet:
                        st.caption(f"Source: {snippet[:120]}")
                    edits[c["field"]] = st.text_input(
                        f"{c['field']} (current: {c['old']})",
                        value=c["proposed"],
                        key=f"editval_{fid}_{c['field']}",
                    )

                col1, col2 = st.columns(2)
                with col1:
                    submitted = st.form_submit_button("Accept with edits", type="primary")
                with col2:
                    cancelled = st.form_submit_button("Cancel")

            if submitted:
                _do_accept(result, edits)
                st.session_state[f"editing_{fid}"] = False
                st.rerun()
            if cancelled:
                st.session_state[f"editing_{fid}"] = False
                st.rerun()


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------

st.title("AIS-tree Updater")

if st.button("Run updater", type="primary"):
    spreadsheet = get_spreadsheet()
    rows = read_main_rows(spreadsheet)
    state_ws = ensure_state_tab(spreadsheet)
    state = read_state(state_ws)
    system_prompt = load_system_prompt()
    schema_desc = build_schema_description(config["fields"])
    llm_client = get_llm_client()

    results = []
    with st.status("Checking fellowships…", expanded=True) as run_status:
        for i, row in enumerate(rows):
            name = row.get("name", row["ID"])
            st.write(f"Checking {i + 1}/{len(rows)}: {name}…")
            result = process_row(row, state, config, llm_client, system_prompt, schema_desc)
            results.append(result)

            # Update state tab
            new_state_row = {
                "id": row["ID"],
                "url": str(row.get("url", "")),
                "last_hash": result.get("new_hash") or state.get(row["ID"], {}).get("last_hash", ""),
                "last_checked_at": result["checked_at"],
                "last_classification": result.get("classification") or result["status"],
                "last_result": "pending" if result["status"] == "pending" else "",
            }
            upsert_state_row(state_ws, state, new_state_row)

            # Update last_verified for all rows that were successfully fetched
            if result["status"] not in ("no_url", "fetch_error"):
                set_last_verified_by_id(spreadsheet, row["ID"])

        run_status.update(label=f"Done — {len(rows)} fellowships checked", state="complete")

    st.session_state.results = results
    st.session_state.review = {}
    st.rerun()

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

if st.session_state.results:
    results = st.session_state.results
    show_reviewed = st.checkbox("Show already reviewed", value=False)

    pending = [r for r in results if r["status"] == "pending"]
    errors = [r for r in results if r["status"] in ("fetch_error", "llm_error")]
    unchanged = [r for r in results if r["status"] == "unchanged"]
    no_url = [r for r in results if r["status"] == "no_url"]

    # Errors first
    if errors:
        st.subheader(f"Errors ({len(errors)})")
        for r in errors:
            with st.container(border=True):
                st.markdown(f"**{r['name']}** — `{r['status']}`")
                st.error(r.get("error", ""))
                if r.get("llm_raw"):
                    with st.expander("Raw LLM output"):
                        st.text(r["llm_raw"])

    # Pending cards grouped by classification
    for cls in STATUS_ORDER:
        group = [r for r in pending if r["classification"] == cls]
        if not group:
            continue
        reviewed_in_group = sum(1 for r in group if r["id"] in st.session_state.review)
        st.subheader(f"{BADGE[cls]} ({len(group)}, {reviewed_in_group} reviewed)")
        for r in group:
            if not show_reviewed and r["id"] in st.session_state.review:
                continue
            _render_card(r)

    # Summary rows
    with st.expander(f"Unchanged ({len(unchanged)})"):
        for r in unchanged:
            st.markdown(f"- {r['name']}")

    if no_url:
        with st.expander(f"No URL ({len(no_url)})"):
            for r in no_url:
                st.markdown(f"- {r['name']}")
