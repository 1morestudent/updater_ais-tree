from datetime import datetime, timezone

import anthropic

from updater.fetch import fetch_and_extract, sha256
from updater.diff import compute_diff, validate_proposed
from updater.llm import call_llm


def fetch_row(row: dict, state: dict, config: dict) -> dict:
    """Phase 1: fetch, extract, and hash. No LLM call."""
    fellowship_id = row["ID"]
    url = str(row.get("url", "")).strip()
    now = datetime.now(timezone.utc).isoformat()

    base = {
        "id": fellowship_id,
        "name": row.get("name", fellowship_id),
        "url": url,
        "checked_at": now,
        "new_hash": None,
        "extracted_text": None,
        "error": None,
    }

    if not url or url == "[unclear]":
        return {**base, "status": "no_url"}

    text, error = fetch_and_extract(
        url,
        timeout=config["fetch"]["timeout_seconds"],
        user_agent=config["fetch"]["user_agent"],
    )
    if error:
        return {**base, "status": "fetch_error", "error": error}

    new_hash = sha256(text)
    old_hash = state.get(fellowship_id, {}).get("last_hash", "")
    triggered = state.get(fellowship_id, {}).get("trigger_check_next_update", "").lower() == "true"

    if new_hash == old_hash and not triggered:
        return {**base, "status": "unchanged", "new_hash": new_hash}

    return {**base, "status": "changed", "new_hash": new_hash, "extracted_text": text, "triggered": triggered}


def process_changed_row(
    row: dict,
    fetch_result: dict,
    config: dict,
    llm_client: anthropic.Anthropic,
    system_prompt: str,
    schema_description: str,
) -> dict:
    """Phase 2: LLM analysis for a row whose content has changed."""
    fellowship_id = row["ID"]

    result = {
        "id": fellowship_id,
        "name": row.get("name", fellowship_id),
        "url": fetch_result["url"],
        "status": None,
        "classification": None,
        "reasoning": None,
        "changes": [],
        "snippets": {},
        "type_warnings": [],
        "triggered": fetch_result.get("triggered", False),
        "new_hash": fetch_result["new_hash"],
        "checked_at": fetch_result["checked_at"],
        "error": None,
        "llm_raw": None,
    }

    llm_response = call_llm(
        llm_client,
        config["model"],
        system_prompt,
        row,
        fetch_result["extracted_text"],
        schema_description,
    )

    if "error" in llm_response:
        result["status"] = "llm_error"
        result["error"] = llm_response["error"]
        result["llm_raw"] = llm_response.get("raw", "")
        return result

    proposed_fields = llm_response.get("proposed_fields", {})
    result["status"] = "pending"
    result["classification"] = llm_response.get("classification", "not_relevant")
    result["reasoning"] = llm_response.get("reasoning", "")
    result["changes"] = compute_diff(row, proposed_fields, config["fields"])
    result["snippets"] = llm_response.get("snippets", {})
    result["type_warnings"] = validate_proposed(proposed_fields, config["fields"])

    return result


def _make_no_llm_result(fr: dict) -> dict:
    return {
        "id": fr["id"],
        "name": fr["name"],
        "url": fr["url"],
        "status": fr["status"],
        "classification": None,
        "reasoning": None,
        "changes": [],
        "snippets": {},
        "type_warnings": [],
        "new_hash": fr.get("new_hash"),
        "checked_at": fr["checked_at"],
        "error": fr.get("error"),
        "llm_raw": None,
    }
