import json
import re
from pathlib import Path

import anthropic


def load_system_prompt() -> str:
    return Path("prompts/classify.md").read_text()


def build_schema_description(fields: list[dict]) -> str:
    lines = []
    for f in fields:
        if f["llm_proposable"]:
            lines.append(f"- {f['name']} ({f['type']}, {f['volatility']}): {f['description']}")
    return "\n".join(lines)


def call_llm(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    row: dict,
    extracted_text: str,
    schema_description: str,
) -> dict:
    notes = str(row.get("notes_for_claude_during_update", "")).strip()
    notes_section = f"\n\nNotes for this fellowship:\n{notes}" if notes else ""

    # Exclude internal/meta fields from the row JSON shown to the LLM
    excluded = {"notes_for_claude_during_update"}
    row_for_llm = {k: v for k, v in row.items() if k not in excluded}

    user_prompt = (
        f"Current fellowship data:\n{json.dumps(row_for_llm, indent=2, default=str)}\n\n"
        f"Extracted page content:\n{extracted_text[:8000]}\n\n"
        f"Proposable fields:\n{schema_description}"
        f"{notes_section}"
    )

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {"error": "no_json", "raw": raw}

    try:
        return json.loads(match.group())
    except json.JSONDecodeError as e:
        return {"error": "parse_error", "raw": raw, "detail": str(e)}
