"""Apply a list of change operations to a policy, producing a new version.

Operations are what the policy reader produces from an amendment document or a
chat message. Each is validated; the result is validated as a whole Policy, so
an amendment can never produce a policy the engine cannot run.

Operation shapes::

    {"op": "set_parameter",   "path": "clubbing.creation_window_days", "value": 21}
    {"op": "add_rule",        "rule": {...}, "before": "R-5.4"}   # or "after", or neither (= end)
    {"op": "update_rule",     "rule_id": "R-5.4", "rule": {...}}  # full replacement
    {"op": "remove_rule",     "rule_id": "R-5.3"}
    {"op": "move_rule",       "rule_id": "R-5.3", "before": "R-5.2"}
    {"op": "add_override",    "override": {...}}
    {"op": "update_override", "override_id": "OV-1", "override": {...}}
    {"op": "remove_override", "override_id": "OV-1"}
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any

from .policy import Override, Policy, Rule, _set_path, _validate_param_path


class AmendmentError(ValueError):
    pass


def _index(rules: list[dict], rule_id: str) -> int:
    for i, r in enumerate(rules):
        if r["id"] == rule_id:
            return i
    raise AmendmentError(f"rule '{rule_id}' does not exist")


def _insert(rules: list[dict], rule: dict, before: str | None, after: str | None) -> None:
    if before:
        rules.insert(_index(rules, before), rule)
    elif after:
        rules.insert(_index(rules, after) + 1, rule)
    else:
        rules.append(rule)


def apply_operations(base: Policy, operations: list[dict[str, Any]], version: str, effective_from: date,
                     summary: str = "") -> Policy:
    doc = copy.deepcopy(base.model_dump(mode="json", by_alias=True))
    doc["version"], doc["effective_from"] = version, effective_from.isoformat()
    doc["summary"] = summary or doc.get("summary", "")
    rules, overrides = doc["rules"], doc["overrides"]

    for n, op in enumerate(operations, start=1):
        kind = op.get("op")
        try:
            if kind == "set_parameter":
                _validate_param_path(op["path"])
                _set_path(doc["parameters"], op["path"], op["value"])
            elif kind == "add_rule":
                rule = Rule.model_validate(op["rule"]).model_dump(mode="json", by_alias=True)
                if any(r["id"] == rule["id"] for r in rules):
                    raise AmendmentError(f"rule id '{rule['id']}' already exists")
                _insert(rules, rule, op.get("before"), op.get("after"))
            elif kind == "update_rule":
                i = _index(rules, op["rule_id"])
                rule = Rule.model_validate({**rules[i], **op["rule"]}).model_dump(mode="json", by_alias=True)
                rules[i] = rule
            elif kind == "remove_rule":
                rules.pop(_index(rules, op["rule_id"]))
            elif kind == "move_rule":
                rule = rules.pop(_index(rules, op["rule_id"]))
                _insert(rules, rule, op.get("before"), op.get("after"))
            elif kind == "add_override":
                ov = Override.model_validate(op["override"]).model_dump(mode="json", by_alias=True)
                if any(o["id"] == ov["id"] for o in overrides):
                    raise AmendmentError(f"override id '{ov['id']}' already exists")
                overrides.append(ov)
            elif kind == "update_override":
                i = next((j for j, o in enumerate(overrides) if o["id"] == op["override_id"]), None)
                if i is None:
                    raise AmendmentError(f"override '{op['override_id']}' does not exist")
                overrides[i] = Override.model_validate({**overrides[i], **op["override"]}).model_dump(mode="json", by_alias=True)
            elif kind == "remove_override":
                before = len(overrides)
                overrides[:] = [o for o in overrides if o["id"] != op["override_id"]]
                if len(overrides) == before:
                    raise AmendmentError(f"override '{op['override_id']}' does not exist")
            else:
                raise AmendmentError(f"unknown operation '{kind}'")
        except AmendmentError as exc:
            raise AmendmentError(f"change {n} ({kind}): {exc}") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise AmendmentError(f"change {n} ({kind}) is invalid: {exc}") from exc
    try:
        return Policy.model_validate(doc)
    except ValueError as exc:
        raise AmendmentError(f"the amended policy is invalid: {exc}") from exc
