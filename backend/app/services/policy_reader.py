"""Read an amendment (document or chat message) into policy change operations.

Claude reads the text against the current policy JSON and the catalogue of
facts / parameters, and returns operations (see ``engine.amend``). The result
is applied and validated; if validation fails the error is sent back once so
the model can correct itself.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..engine.amend import AmendmentError, apply_operations
from ..engine.policy import FACTS, Policy, parameter_catalogue
from .llm import LLMUnavailable, extract_json

SYSTEM = """You convert procurement policy amendments into precise, machine-readable change operations.

The company's procurement policy is stored as JSON with:
- parameters: numeric/boolean settings (look-back windows, clubbing windows, ranking weights, consolidation window...)
- overrides: conditional parameter settings that apply only when a context condition matches
  (context facts: plant, category, item_type, item_code, urgency, is_emergency, department)
- rules: an ORDERED decision table. The first enabled rule whose `when` condition is true decides the outcome.

A condition is a leaf {"field": <fact>, "op": <op>, "value": <value>} or a group {"all": [...]}, {"any": [...]}, {"not": {...}}.
Ops: eq, ne, lt, lte, gt, gte, in, not_in, contains, exists. Values in INR are plain numbers (5,00,000 -> 500000; 2 lakh -> 200000; 1 crore -> 10000000).
"below X" -> lt, "X or more"/"at least X" -> gte, "up to X"/"not more than X" -> lte.

An outcome is {"decision": "PO"|"SOURCING_REQUEST"|"NOT_PROCESSED", "route": <ROUTE>, "supplier": "contract"|"preferred"|"suggest"|"none", "suggest_count": int, "note": str|null}.
- PO outcomes must use supplier "contract" (contract supplier at contract rate) or "preferred" (1st preferred supplier at its reference price).
- Sourcing outcomes use "suggest" with a suggest_count, or "none".
- Existing routes: CONTRACT_PO, EMERGENCY_PO, SPOT_PO, RFQ, RFP, CAPEX_SOURCING, NEW_ITEM_SOURCING. Reuse them; invent a new route name only if the amendment clearly defines a new route.

Rules have: id (stable, e.g. "R-5.4"; give new rules a new id like "R-5.3A"), clause (the policy clause number as written in the amendment), title, when, outcome, enabled.

Guidance:
- Change only what the amendment changes. Keep everything else.
- To change a threshold or condition of an existing rule, use update_rule with the COMPLETE new rule (id, clause, title, when, outcome).
- A rule that applies only to one plant / category / item type is a NEW rule (add_rule) placed BEFORE the general rule it takes precedence over, with the restriction in its condition - do not edit the general rule.
- A changed window, look-back, weight or count that applies everywhere is set_parameter. If it applies only to one plant/category, use add_override.
- If a sentence cannot be expressed with the available facts, parameters and operations, list it in `unmapped` - never guess.
- effective_from: the date the amendment takes effect (YYYY-MM-DD), or null if not stated. version: the new version label if stated (e.g. "5.1"), else null.
- Explain each operation in one plain-English sentence a buyer would understand.
"""

OP_SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": [
            "set_parameter", "add_rule", "update_rule", "remove_rule", "move_rule",
            "add_override", "update_override", "remove_override",
        ]},
        "path": {"type": ["string", "null"]},
        "value_json": {"type": ["string", "null"], "description": "JSON-encoded parameter value"},
        "rule_id": {"type": ["string", "null"]},
        "override_id": {"type": ["string", "null"]},
        "before": {"type": ["string", "null"], "description": "rule id to insert/move before"},
        "after": {"type": ["string", "null"], "description": "rule id to insert/move after"},
        "rule_json": {"type": ["string", "null"], "description": "JSON-encoded complete rule"},
        "override_json": {"type": ["string", "null"], "description": "JSON-encoded override {id, description, when, set}"},
        "explanation": {"type": "string"},
    },
    "required": ["op", "path", "value_json", "rule_id", "override_id", "before", "after", "rule_json",
                 "override_json", "explanation"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "version": {"type": ["string", "null"]},
        "effective_from": {"type": ["string", "null"]},
        "summary": {"type": "string"},
        "operations": {"type": "array", "items": OP_SCHEMA},
        "unmapped": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["version", "effective_from", "summary", "operations", "unmapped"],
    "additionalProperties": False,
}


@dataclass
class ReadResult:
    policy: Policy
    operations: list[dict[str, Any]]
    explanations: list[str]
    summary: str
    warnings: list[str] = field(default_factory=list)
    version_given: bool = False
    date_given: bool = False


def document_text(filename: str, data: bytes) -> str:
    """Plain text from an uploaded amendment (.pdf, .docx, .txt, .md, .json)."""
    name = filename.lower()
    if name.endswith(".pdf"):
        from .contract_pdf import pdf_text

        return pdf_text(data)
    if name.endswith(".docx"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    return data.decode("utf-8-sig", errors="replace")


def next_version(current: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)$", current or "")
    return f"{m.group(1)}.{int(m.group(2)) + 1}" if m else f"{current}-amended"


def _decode_operation(raw: dict[str, Any]) -> dict[str, Any]:
    op: dict[str, Any] = {"op": raw["op"]}
    for key in ("path", "rule_id", "override_id", "before", "after"):
        if raw.get(key):
            op[key] = raw[key]
    for key, target in (("value_json", "value"), ("rule_json", "rule"), ("override_json", "override")):
        if raw.get(key) is not None:
            op[target] = json.loads(raw[key])
    return op


def read_amendment(text: str, base: Policy, today: date) -> ReadResult:
    if not text.strip():
        raise AmendmentError("the amendment is empty")
    prompt = (
        f"Today is {today.isoformat()}.\n\n"
        f"CURRENT POLICY (version {base.version}):\n{json.dumps(base.model_dump(mode='json', by_alias=True), indent=1)}\n\n"
        f"FACTS a rule condition can test:\n{json.dumps(FACTS, indent=1)}\n\n"
        f"PARAMETERS (dotted path: current default):\n{json.dumps(parameter_catalogue(), indent=1)}\n\n"
        f"AMENDMENT / CHANGE REQUEST:\n<<<\n{text.strip()}\n>>>"
    )
    attempt_error = ""
    for _ in range(2):
        data = extract_json(SYSTEM, prompt + attempt_error, SCHEMA, effort="high")
        try:
            operations = [_decode_operation(o) for o in data["operations"]]
            version = data.get("version") or next_version(base.version)
            eff_raw = data.get("effective_from")
            effective = date.fromisoformat(eff_raw) if eff_raw else today
            policy = apply_operations(base, operations, version, effective, data.get("summary", ""))
        except (AmendmentError, ValueError, KeyError, json.JSONDecodeError) as exc:
            attempt_error = (
                f"\n\nYour previous answer could not be applied: {exc}. "
                "Return corrected operations that pass validation."
            )
            continue
        warnings = [f"Not applied (cannot be expressed): {u}" for u in data.get("unmapped", [])]
        if not eff_raw:
            warnings.append("The amendment does not state an effective date; please set one before approving.")
        if not operations:
            warnings.append("No changes were found in the text.")
        return ReadResult(
            policy=policy,
            operations=operations,
            explanations=[o["explanation"] for o in data["operations"]],
            summary=data.get("summary", ""),
            warnings=warnings,
            version_given=bool(data.get("version")),
            date_given=bool(eff_raw),
        )
    raise AmendmentError(f"could not turn the amendment into valid changes ({attempt_error.strip()})")


__all__ = ["read_amendment", "document_text", "next_version", "ReadResult", "LLMUnavailable"]
