import json
from collections import Counter

import anthropic
import pandas as pd
import streamlit as st

from updater.llm import build_schema_description, load_system_prompt
from updater.pipeline import fetch_row, process_changed_row, _make_no_llm_result
from updater.sheets import (
    accept_fellowship,
    batch_set_last_verified,
    ensure_proposals_tab,
    ensure_state_tab,
    open_sheet,
    read_main_rows,
    read_pending_proposals,
    read_state,
    supersede_pending_proposals,
    update_proposal_status,
    update_state_result,
    upsert_state_row,
    set_trigger_flag,
    write_proposal_rows,
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
    if "\\n" in sa.get("private_key", ""):
        sa["private_key"] = sa["private_key"].replace("\\n", "\n")
    return open_sheet(sa, st.secrets["SHEET_ID"])


@st.cache_resource
def get_llm_client():
    return anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "run_phase": None,       # None | "phase1_done" | "complete"
    "phase1_results": [],
    "phase1_rows": {},
    "phase1_state": {},
    "results": [],
    "review": {},
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


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
    proposals_ws = ensure_proposals_tab(spreadsheet)
    accept_fellowship(spreadsheet, result["id"], updates)
    update_state_result(state_ws, result["id"], "accepted")
    update_proposal_status(proposals_ws, result["id"], "accepted")
    st.session_state.review[result["id"]] = {"action": "accepted"}


def _do_reject(result: dict) -> None:
    spreadsheet = get_spreadsheet()
    state_ws = ensure_state_tab(spreadsheet)
    proposals_ws = ensure_proposals_tab(spreadsheet)
    update_state_result(state_ws, result["id"], "rejected")
    update_proposal_status(proposals_ws, result["id"], "rejected")
    st.session_state.review[result["id"]] = {"action": "rejected"}


def _render_card(result: dict) -> None:
    fid = result["id"]
    review = st.session_state.review.get(fid)

    with st.container(border=True):
        col_name, col_badge = st.columns([4, 1])
        with col_name:
            triggered_label = "  _(forced re-check)_" if result.get("triggered") else ""
            st.markdown(f"**[{result['name']}]({result['url']})**  `{fid}`{triggered_label}")
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
            for tw in result.get("type_warnings", []):
                st.warning(f"Type warning — **{tw['field']}**: {tw['warning']}")

            table_data = {
                c["field"]: {
                    "Current": c["old"],
                    "Proposed": c["proposed"],
                    "Source snippet": result["snippets"].get(c["field"], ""),
                }
                for c in result["changes"]
            }
            st.table(pd.DataFrame(table_data).T)

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
            with st.form(key=f"form_{fid}"):
                edits = {}
                for c in result["changes"]:
                    snippet = result["snippets"].get(c["field"], "")
                    if snippet:
                        st.caption(f"Source: {snippet}")
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

# ── Phase 0: idle — resume check + "Run updater" button ───────────────────
if st.session_state.run_phase is None:
    spreadsheet = get_spreadsheet()
    proposals_ws = ensure_proposals_tab(spreadsheet)
    pending_results = read_pending_proposals(proposals_ws)

    if pending_results:
        n = len(pending_results)
        st.info(
            f"**{n} fellowship{'s' if n != 1 else ''}** with pending proposed changes from the last run."
        )
        col_resume, col_run = st.columns(2)
        with col_resume:
            resume_clicked = st.button(f"Resume pending review ({n})", type="primary")
        with col_run:
            run_clicked = st.button("Run updater (start fresh)")
    else:
        resume_clicked = False
        run_clicked = st.button("Run updater", type="primary")

    if resume_clicked:
        st.session_state.results = pending_results
        st.session_state.review = {}
        st.session_state.run_phase = "complete"
        st.rerun()

    if run_clicked:
        supersede_pending_proposals(proposals_ws)
        rows = read_main_rows(spreadsheet)

        id_counts = Counter(row.get("ID", "") for row in rows)
        duplicates = sorted(id_ for id_, n in id_counts.items() if id_ and n > 1)
        if duplicates:
            st.error(f"Duplicate IDs in sheet — fix before running: **{', '.join(duplicates)}**")
            st.stop()

        state_ws = ensure_state_tab(spreadsheet)
        state = read_state(state_ws)

        phase1_results = []
        verified_ids = []
        with st.status("Fetching fellowship pages…", expanded=True) as run_status:
            for i, row in enumerate(rows):
                name = row.get("name", row["ID"])
                st.write(f"Fetching {i + 1}/{len(rows)}: {name}…")
                fr = fetch_row(row, state, config)
                phase1_results.append(fr)

                # Commit unchanged rows immediately — no LLM needed
                if fr["status"] == "unchanged":
                    new_state_row = {
                        "id": row["ID"],
                        "url": str(row.get("url", "")),
                        "last_hash": fr["new_hash"],
                        "last_checked_at": fr["checked_at"],
                        "last_classification": state.get(row["ID"], {}).get("last_classification", ""),
                        "last_result": "",
                    }
                    upsert_state_row(state_ws, state, new_state_row)
                    verified_ids.append(row["ID"])

            batch_set_last_verified(spreadsheet, verified_ids)
            changed_count = sum(1 for r in phase1_results if r["status"] == "changed")
            run_status.update(
                label=f"Fetch complete — {changed_count} changed, {len(phase1_results) - changed_count} other",
                state="complete",
            )

        st.session_state.phase1_results = phase1_results
        st.session_state.phase1_rows = {row["ID"]: row for row in rows}
        st.session_state.phase1_state = state
        st.session_state.run_phase = "phase1_done"
        st.rerun()

# ── Phase 1 done: show summary + confirm button ────────────────────────────
elif st.session_state.run_phase == "phase1_done":
    phase1 = st.session_state.phase1_results
    changed = [r for r in phase1 if r["status"] == "changed"]
    unchanged = [r for r in phase1 if r["status"] == "unchanged"]
    errors = [r for r in phase1 if r["status"] == "fetch_error"]
    no_url = [r for r in phase1 if r["status"] == "no_url"]

    st.subheader("Fetch complete")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Changed", len(changed))
    c2.metric("Unchanged", len(unchanged))
    c3.metric("Errors", len(errors))
    c4.metric("No URL", len(no_url))

    if errors:
        with st.expander(f"Fetch errors ({len(errors)})"):
            for r in errors:
                st.markdown(f"- **{r['name']}**: {r['error']}")

    if not changed:
        st.info("No changes detected — nothing to analyse.")
        if st.button("Reset"):
            st.session_state.run_phase = None
            st.rerun()
    else:
        est_cost = len(changed) * 0.03
        st.info(
            f"**{len(changed)} changed** entr{'y' if len(changed) == 1 else 'ies'} ready for LLM analysis. "
            f"Estimated cost: ~${est_cost:.2f}."
        )
        col_confirm, col_cancel = st.columns(2)
        with col_confirm:
            if st.button(f"Analyse {len(changed)} {'entry' if len(changed) == 1 else 'entries'}", type="primary"):
                spreadsheet = get_spreadsheet()
                state_ws = ensure_state_tab(spreadsheet)
                proposals_ws = ensure_proposals_tab(spreadsheet)
                system_prompt = load_system_prompt()
                schema_desc = build_schema_description(config["fields"])
                llm_client = get_llm_client()
                state = st.session_state.phase1_state
                phase1_rows = st.session_state.phase1_rows

                results = []
                verified_ids = []
                with st.status("Analysing changed entries…", expanded=True) as run_status:
                    for i, fr in enumerate(changed):
                        row = phase1_rows[fr["id"]]
                        name = row.get("name", fr["id"])
                        st.write(f"Analysing {i + 1}/{len(changed)}: {name}…")
                        result = process_changed_row(row, fr, config, llm_client, system_prompt, schema_desc)
                        results.append(result)

                        new_state_row = {
                            "id": fr["id"],
                            "url": fr["url"],
                            "last_hash": fr["new_hash"],
                            "last_checked_at": fr["checked_at"],
                            "last_classification": result.get("classification") or result["status"],
                            "last_result": "pending" if result["status"] == "pending" else "",
                            "trigger_check_next_update": "",  # clear trigger after LLM run
                        }
                        upsert_state_row(state_ws, state, new_state_row)
                        verified_ids.append(fr["id"])
                        write_proposal_rows(proposals_ws, result)

                    batch_set_last_verified(spreadsheet, verified_ids)
                    run_status.update(
                        label=f"Done — {len(changed)} entries analysed",
                        state="complete",
                    )

                # Include no_url and fetch_error rows in the results for display
                for fr in no_url + errors:
                    results.append(_make_no_llm_result(fr))

                st.session_state.results = results
                st.session_state.review = {}
                st.session_state.run_phase = "complete"
                st.rerun()

        with col_cancel:
            if st.button("Cancel"):
                st.session_state.run_phase = None
                st.rerun()

# ── Phase 2 complete: show results ────────────────────────────────────────
elif st.session_state.run_phase == "complete":
    if st.button("Run updater again"):
        for k in ("run_phase", "phase1_results", "phase1_rows", "phase1_state", "results", "review"):
            st.session_state[k] = _DEFAULTS[k]
        st.rerun()

    results = st.session_state.results
    show_reviewed = st.checkbox("Show already reviewed", value=False)

    pending = [r for r in results if r["status"] == "pending"]
    errors = [r for r in results if r["status"] in ("fetch_error", "llm_error")]
    unchanged = [r for r in st.session_state.phase1_results if r["status"] == "unchanged"]
    no_url = [r for r in results if r["status"] == "no_url"]

    if errors:
        st.subheader(f"Errors ({len(errors)})")
        for r in errors:
            with st.container(border=True):
                st.markdown(f"**{r['name']}** — `{r['status']}`")
                st.error(r.get("error", ""))
                if r.get("llm_raw"):
                    with st.expander("Raw LLM output"):
                        st.text(r["llm_raw"])

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

    with st.expander(f"Unchanged ({len(unchanged)})"):
        for r in unchanged:
            st.markdown(f"- {r['name']}")

    if no_url:
        with st.expander(f"No URL ({len(no_url)})"):
            for r in no_url:
                st.markdown(f"- {r['name']}")
