"""CSV and Excel file parser for bulk import.

Each public function returns a normalised result dict:

    {
        "rows":          list[dict],   # validated, type-coerced records
        "errors":        list[str],    # row-level or file-level error messages
        "total_parsed":  int,          # rows read (excluding header)
        "valid_count":   int,          # rows that passed all validation
    }

Supported entity types are defined in :data:`ENTITY_SCHEMAS`.
"""

import csv
import io
from typing import Any


# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

# Each schema entry maps an entity type name to an ordered list of field
# descriptors.  Field descriptors are plain dicts with the following keys:
#
#   name      (str)  – column header expected in the file (case-insensitive)
#   required  (bool) – whether the column must have a non-empty value
#   type      (str)  – one of "str", "int", "float", "bool"
#   default   (Any)  – value used when the column is empty and not required

ENTITY_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "features": [
        {"name": "name", "required": True, "type": "str"},
        {"name": "category", "required": False, "type": "str"},
        {"name": "complexity_weight", "required": False, "type": "float", "default": 1.0},
        {"name": "has_existing_tests", "required": False, "type": "bool", "default": False},
        {"name": "description", "required": False, "type": "str"},
    ],
    "dut_types": [
        {"name": "name", "required": True, "type": "str"},
        {"name": "category", "required": False, "type": "str"},
        {"name": "complexity_multiplier", "required": False, "type": "float", "default": 1.0},
    ],
    "test_profiles": [
        {"name": "name", "required": True, "type": "str"},
        {"name": "description", "required": False, "type": "str"},
        {"name": "effort_multiplier", "required": False, "type": "float", "default": 1.0},
    ],
    "task_templates": [
        {"name": "name", "required": True, "type": "str"},
        {"name": "task_type", "required": True, "type": "str"},
        {"name": "base_effort_hours", "required": True, "type": "float"},
        {"name": "scales_with_dut", "required": False, "type": "bool", "default": False},
        {"name": "scales_with_profile", "required": False, "type": "bool", "default": False},
        {"name": "is_parallelizable", "required": False, "type": "bool", "default": False},
        {"name": "description", "required": False, "type": "str"},
    ],
    "historical_projects": [
        {"name": "project_name", "required": True, "type": "str"},
        {"name": "project_type", "required": True, "type": "str"},
        {"name": "estimated_hours", "required": False, "type": "float"},
        {"name": "actual_hours", "required": False, "type": "float"},
        {"name": "dut_count", "required": False, "type": "int"},
        {"name": "profile_count", "required": False, "type": "int"},
        {"name": "pr_count", "required": False, "type": "int"},
        {"name": "completion_date", "required": False, "type": "str"},
        {"name": "notes", "required": False, "type": "str"},
    ],
}


# ---------------------------------------------------------------------------
# Type coercion helpers
# ---------------------------------------------------------------------------

def _parse_bool(value: str) -> bool:
    """Interpret common truthy string representations as True."""
    return value.strip().lower() in ("true", "1", "yes", "y")


def _coerce_value(value: str, field_type: str, default: Any = None) -> Any:
    """Coerce a string value to the target Python type.

    Args:
        value:      Raw string extracted from the file.
        field_type: One of ``"str"``, ``"int"``, ``"float"``, ``"bool"``.
        default:    Fallback value when *value* is empty.

    Returns:
        The coerced value, or *default* when the input is empty.

    Raises:
        ValueError: When the string cannot be converted to the target type.
        TypeError:  When an unexpected type conversion is attempted.
    """
    value = value.strip()
    if not value:
        return default

    match field_type:
        case "float":
            return float(value)
        case "int":
            return int(value)
        case "bool":
            return _parse_bool(value)
        case _:
            return value


# ---------------------------------------------------------------------------
# Shared validation logic
# ---------------------------------------------------------------------------

def _build_empty_result() -> dict[str, Any]:
    return {"rows": [], "errors": [], "total_parsed": 0, "valid_count": 0}


def _validate_headers(
    schema: list[dict[str, Any]], actual_headers: set[str]
) -> list[str]:
    """Return a list of error strings for missing required headers."""
    required = {f["name"] for f in schema if f.get("required")}
    missing = required - actual_headers
    if missing:
        return [f"Missing required columns: {', '.join(sorted(missing))}"]
    return []


def _process_row(
    schema: list[dict[str, Any]],
    row_dict: dict[str, str],
    row_num: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate and coerce a single row against *schema*.

    Returns:
        A tuple ``(row_data, errors)``.  *row_data* is ``None`` when any
        error was encountered; otherwise it is a fully-coerced dict.
    """
    row_data: dict[str, Any] = {}
    errors: list[str] = []

    for field in schema:
        name: str = field["name"]
        raw_value: str = str(row_dict.get(name, "")).strip()
        required: bool = field.get("required", False)
        default: Any = field.get("default")

        if required and not raw_value:
            errors.append(f"Row {row_num}: Missing required field '{name}'")
            continue

        try:
            row_data[name] = _coerce_value(raw_value, field["type"], default)
        except (ValueError, TypeError) as exc:
            errors.append(
                f"Row {row_num}: Invalid value for '{name}': {raw_value!r} ({exc})"
            )

    return (None if errors else row_data), errors


# ---------------------------------------------------------------------------
# Public parsers
# ---------------------------------------------------------------------------

def parse_csv(content: bytes, entity_type: str) -> dict[str, Any]:
    """Parse UTF-8 (or Latin-1) CSV content and validate against the schema.

    BOM-prefixed files (UTF-8-sig) are handled transparently.

    Args:
        content:     Raw file bytes.
        entity_type: Key into :data:`ENTITY_SCHEMAS`.

    Returns:
        Result dict with keys ``rows``, ``errors``, ``total_parsed``,
        ``valid_count``.
    """
    schema = ENTITY_SCHEMAS.get(entity_type)
    if not schema:
        result = _build_empty_result()
        result["errors"].append(f"Unknown entity type: {entity_type}")
        return result

    result = _build_empty_result()

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames:
        actual_headers = {h.strip().lower() for h in reader.fieldnames}
        header_errors = _validate_headers(schema, actual_headers)
        if header_errors:
            result["errors"].extend(header_errors)
            return result

    for row_num, raw_row in enumerate(reader, start=2):  # row 1 is the header
        result["total_parsed"] += 1
        # Normalise header case for case-insensitive matching
        normalised: dict[str, str] = {
            k.strip().lower(): v for k, v in raw_row.items()
        }
        row_data, row_errors = _process_row(schema, normalised, row_num)
        if row_errors:
            result["errors"].extend(row_errors)
        else:
            result["rows"].append(row_data)
            result["valid_count"] += 1

    return result


def parse_excel(content: bytes, entity_type: str) -> dict[str, Any]:
    """Parse an Excel (.xlsx) workbook and validate against the schema.

    Only the *active* worksheet is processed.  The first row is treated as
    the header row.

    Args:
        content:     Raw ``.xlsx`` file bytes.
        entity_type: Key into :data:`ENTITY_SCHEMAS`.

    Returns:
        Result dict with keys ``rows``, ``errors``, ``total_parsed``,
        ``valid_count``.
    """
    schema = ENTITY_SCHEMAS.get(entity_type)
    if not schema:
        result = _build_empty_result()
        result["errors"].append(f"Unknown entity type: {entity_type}")
        return result

    result = _build_empty_result()

    try:
        import openpyxl  # optional dependency — only needed for Excel imports

        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        ws = wb.active
        if ws is None:
            result["errors"].append("No active worksheet found")
            return result

        all_rows = list(ws.iter_rows(values_only=True))
        if not all_rows:
            result["errors"].append("Empty worksheet")
            return result

        headers = [
            str(h).strip().lower() if h is not None else "" for h in all_rows[0]
        ]
        header_errors = _validate_headers(schema, set(headers))
        if header_errors:
            result["errors"].extend(header_errors)
            wb.close()
            return result

        for row_num, row_values in enumerate(all_rows[1:], start=2):
            result["total_parsed"] += 1
            row_dict: dict[str, str] = {
                col: (str(val).strip() if val is not None else "")
                for col, val in zip(headers, row_values)
            }
            row_data, row_errors = _process_row(schema, row_dict, row_num)
            if row_errors:
                result["errors"].extend(row_errors)
            else:
                result["rows"].append(row_data)
                result["valid_count"] += 1

        wb.close()

    except Exception as exc:
        result["errors"].append(f"Failed to parse Excel file: {exc}")

    return result
