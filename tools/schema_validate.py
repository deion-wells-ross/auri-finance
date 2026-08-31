"""Minimal, dependency-free structural check used to enforce Section 6's
"structured outputs — non-negotiable, used everywhere" rule and Section 12's
"structured-output schema validation on every handoff — a failure is a bug
regardless of how reasonable the content looks."

Found live in M6: the CFO Agent's first submit_briefing call passed
CDATA-wrapped multi-line strings for fields declared as arrays of strings —
content that reads fine to a human but silently violates its own declared
tool schema, because the Messages API guides generation toward a schema, it
doesn't enforce it. Nothing in this codebase's prior agents checked a
terminal report's shape before accepting it as final. This does: required
fields and top-level type correctness against the tool's own input_schema,
handed back as a specific, actionable tool_result instead of an implicit
pass — so a malformed report gets rejected and retried in the same run,
not shipped.

Only checks what this codebase's schemas actually use (top-level required
fields, and top-level type — including a ["type", "null"] union) — not full
recursive JSON Schema validation, which nothing here needs.
"""

from __future__ import annotations

_PY_TYPES = {
    "array": list,
    "string": str,
    "object": dict,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


def first_schema_violation(tool_input: dict, schema: dict) -> str | None:
    """Returns a human-readable description of the first violation found
    against a tool's `input_schema`, or None if tool_input conforms."""
    properties = schema.get("properties", {})

    for field in schema.get("required", []):
        if field not in tool_input:
            return f"missing required field '{field}'"

    for field, value in tool_input.items():
        prop_schema = properties.get(field)
        if not prop_schema:
            continue
        declared = prop_schema.get("type")
        if declared is None:
            continue
        allowed = declared if isinstance(declared, list) else [declared]
        if "null" in allowed and value is None:
            continue
        py_types = tuple(_PY_TYPES[t] for t in allowed if t in _PY_TYPES and t != "null")
        if py_types and not isinstance(value, py_types):
            non_null = [t for t in allowed if t != "null"]
            return (f"field '{field}' should be of type {non_null}, got {type(value).__name__} "
                    f"({str(value)[:80]!r}) — retry with the correct shape (e.g. a JSON array of "
                    f"distinct strings, not one multi-line string)")
    return None
