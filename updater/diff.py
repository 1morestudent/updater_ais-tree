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


def _normalize_weeks_int(value: str) -> str:
    s = value.strip().lower()
    if s == "<1":
        return "<1"
    s = re.sub(r"\s*(weeks?|wks?)\s*$", "", s).strip()
    try:
        return str(int(float(s)))
    except ValueError:
        return s


def _normalize_hrs_per_week(value: str) -> str:
    s = value.strip().lower()
    if s in ("full-time", "fulltime", "full time"):
        return "40"
    s = re.sub(r"\s*(hrs?|hours?)\s*/\s*week\s*$", "", s).strip()
    try:
        return str(int(float(s)))
    except ValueError:
        return s


def _normalize_usd_int(value: str) -> str:
    # "$8,400/month" → "8400", "8400" → "8400", "none" → "0"
    s = value.strip().lstrip("$").replace(",", "").split("/")[0].split(" ")[0].strip()
    if s.lower() in ("none", "n/a", "no", "0"):
        return "0"
    try:
        return str(int(float(s)))
    except ValueError:
        return s.lower()


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
    if field_type == "usd_int":
        return _normalize_usd_int(s)
    if field_type == "weeks_int":
        return _normalize_weeks_int(s)
    if field_type == "hrs_per_week_int":
        return _normalize_hrs_per_week(s)
    return s.lower()


def validate_value(value: str, field_type: str, allowed_values: list[str] | None = None) -> str | None:
    """Return a warning string if value fails type validation, or None if valid."""
    s = str(value).strip()
    if s == "" or s.lower() == "[unclear]":
        return None
    if field_type == "binary_int":
        if s not in ("0", "1"):
            return f"expected 0 or 1, got {s!r}"
    elif field_type == "date_or_unclear":
        norm = _normalize_date(s)
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", norm):
            return f"expected YYYY-MM-DD, got {s!r}"
    elif field_type == "usd_int":
        norm = _normalize_usd_int(s)
        try:
            int(norm)
        except ValueError:
            return f"expected integer USD (e.g. 8400), got {s!r}"
    elif field_type == "weeks_int":
        if s != "<1":
            try:
                int(_normalize_weeks_int(s))
            except ValueError:
                return f"expected integer weeks or '<1', got {s!r}"
    elif field_type == "hrs_per_week_int":
        try:
            int(_normalize_hrs_per_week(s))
        except ValueError:
            return f"expected integer hrs/week, got {s!r}"
    if allowed_values and s.lower() not in [v.lower() for v in allowed_values]:
        return f"expected one of {allowed_values!r}, got {s!r}"
    return None


def validate_proposed(proposed_fields: dict, field_schema: list[dict]) -> list[dict]:
    """Return list of {field, value, warning} for type-mismatched proposed values."""
    type_map = {f["name"]: f["type"] for f in field_schema}
    allowed_map = {f["name"]: f.get("allowed_values") for f in field_schema}
    warnings = []
    for field, value in proposed_fields.items():
        field_type = type_map.get(field, "string")
        allowed = allowed_map.get(field)
        warning = validate_value(str(value), field_type, allowed)
        if warning:
            warnings.append({"field": field, "value": str(value), "warning": warning})
    return warnings


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
