import re
from datetime import datetime


_DATE_FORMATS = [
    "%Y-%m-%d",
    "%d %B %Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %b %Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%B %Y",
    "%b %Y",
]


def _normalize_date(value: str) -> str:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value.lower().strip()


def _normalize_tags(value: str) -> str:
    parts = [t.strip().lower() for t in value.split(",") if t.strip()]
    return ",".join(sorted(parts))


def normalize(value: str, field_type: str) -> str:
    s = str(value).strip()
    if s == "" or s.lower() == "[unclear]":
        return s.lower()
    if field_type == "date_or_unclear":
        return _normalize_date(s)
    if field_type == "comma_separated":
        return _normalize_tags(s)
    if field_type == "binary_int":
        return str(int(float(s))) if re.match(r"^\d+(\.\d+)?$", s) else s.lower()
    return s.lower()


def compute_diff(current_row: dict, proposed_fields: dict, field_schema: list[dict]) -> list[dict]:
    """Return list of {field, old, proposed} for fields where values meaningfully differ."""
    type_map = {f["name"]: f["type"] for f in field_schema}
    changes = []
    for field, proposed_value in proposed_fields.items():
        current_value = str(current_row.get(field, ""))
        field_type = type_map.get(field, "string")
        if normalize(current_value, field_type) != normalize(str(proposed_value), field_type):
            changes.append({
                "field": field,
                "old": current_value,
                "proposed": str(proposed_value),
            })
    return changes
