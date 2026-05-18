from datetime import datetime, timezone

import anthropic

from updater.fetch import fetch_and_extract, sha256
from updater.diff import compute_diff
from updater.llm import call_llm


def process_row(
    row: dict,
    state: dict,
    config: dict,
    llm_client: anthropic.Anthropic,
    system_prompt: str,
    schema_description: str,
) -> dict:
    fellowship_id = row["ID"]
    url = str(row.get("url", "")).strip()
    now = datetime.now(timezone.utc).isoformat()

    result = {
        "id": fellowship_id,
        "name": row.get("name", fellowship_id),
        "url": url,
        "status": None,
        "classification": None,
        "reasoning": None,
        "changes": [],
        "snippets": {},
        "new_hash": None,
        "checked_at": now,
        "error": None,
        "llm_raw": None,
    }

    if not url or url == "[unclear]":
        result["status"] = "no_url"
        return result

    text, error = fetch_and_extract(
        url,
        timeout=config["fetch"]["timeout_seconds"],
        user_agent=config["fetch"]["user_agent"],
    )
    if error:
        result["status"] = "fetch_error"
        result["error"] = error
        return result

    new_hash = sha256(text)
    result["new_hash"] = new_hash

    old_hash = state.get(fellowship_id, {}).get("last_hash", "")
    if new_hash == old_hash:
        result["status"] = "unchanged"
        return result

    llm_response = call_llm(llm_client, config["model"], system_prompt, row, text, schema_description)

    if "error" in llm_response:
        result["status"] = "llm_error"
        result["error"] = llm_response["error"]
        result["llm_raw"] = llm_response.get("raw", "")
        return result

    proposed_fields = llm_response.get("proposed_fields", {})
    changes = compute_diff(row, proposed_fields, config["fields"])

    result["status"] = "pending"
    result["classification"] = llm_response.get("classification", "not_relevant")
    result["reasoning"] = llm_response.get("reasoning", "")
    result["changes"] = changes
    result["snippets"] = llm_response.get("snippets", {})

    return result
