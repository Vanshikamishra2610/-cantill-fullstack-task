"""Turn policy JSON into plain language, and diff two versions.

Used to show Procurement what an amendment will change before they approve it.
"""
from __future__ import annotations

from typing import Any

from .pipeline import fmt_inr
from .policy import Condition, Override, Policy, Rule, parameter_catalogue

FACT_LABELS = {
    "item_code": "item code",
    "item_type": "item type",
    "category": "category",
    "plant": "plant",
    "value": "requirement value",
    "quantity": "quantity",
    "uom": "unit",
    "urgency": "urgency",
    "line_count": "number of clubbed lines",
    "lead_time_days": "lead time (days)",
    "preferred_supplier_count": "number of preferred suppliers",
    "department": "department",
    "requirement_date": "requirement date",
}
BOOL_TEXT = {
    "is_emergency": ("it is an emergency", "it is not an emergency"),
    "is_new_item": ("it is a new item", "it is an item-master item"),
    "contract_available": ("a contract with enough ceiling applies", "no usable contract applies"),
    "contract_applicable": ("a rate contract applies (ignoring ceiling)", "no rate contract applies"),
    "bought_recently": ("it was bought from an Active supplier recently", "it was not bought from an Active supplier recently"),
}
OP_TEXT = {"eq": "is", "ne": "is not", "lt": "is below", "lte": "is at most", "gt": "is above",
           "gte": "is at least", "in": "is one of", "not_in": "is not one of", "contains": "contains"}
ROUTE_TEXT = {"PO": "Purchase order", "SOURCING_REQUEST": "Sourcing request", "NOT_PROCESSED": "Not processed"}


def _value(field: str, v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(_value(field, x) for x in v)
    if field == "value" and isinstance(v, (int, float)):
        return f"INR {fmt_inr(v)}"
    return str(v)


def describe_condition(c: Condition) -> str:
    if c.all is not None:
        return " and ".join(describe_condition(x) for x in c.all)
    if c.any is not None:
        return "(" + " or ".join(describe_condition(x) for x in c.any) + ")"
    if c.not_ is not None:
        return f"not ({describe_condition(c.not_)})"
    if c.field in BOOL_TEXT and c.op in ("eq", "ne"):
        truthy = bool(c.value) == (c.op == "eq")
        return BOOL_TEXT[c.field][0 if truthy else 1]
    if c.op == "exists":
        return f"{FACT_LABELS.get(c.field, c.field)} is {'given' if c.value in (None, True) else 'missing'}"
    return f"{FACT_LABELS.get(c.field, c.field)} {OP_TEXT[c.op]} {_value(c.field, c.value)}"


def describe_outcome(rule: Rule) -> str:
    o = rule.outcome
    text = f"{ROUTE_TEXT.get(o.decision, o.decision)} - {o.route}"
    if o.supplier == "contract":
        text += ", to the contract supplier at the contract rate"
    elif o.supplier == "preferred":
        text += ", to the 1st preferred supplier at its reference price"
    elif o.supplier == "suggest" and o.suggest_count:
        text += f", suggest {o.suggest_count} suppliers"
    if o.note:
        text += f". {o.note}"
    return text


def describe_rule(rule: Rule) -> str:
    state = "" if rule.enabled else " [disabled]"
    return f"{rule.clause}{state}: if {describe_condition(rule.when)} -> {describe_outcome(rule)}"


def describe_override(ov: Override) -> str:
    sets = ", ".join(f"{k} = {v}" for k, v in ov.set.items())
    return f"When {describe_condition(ov.when)}: {sets}"


def describe_policy(p: Policy) -> dict[str, Any]:
    return {
        "rules": [{"id": r.id, "clause": r.clause, "title": r.title, "text": describe_rule(r)} for r in p.rules],
        "overrides": [{"id": o.id, "text": describe_override(o), "description": o.description} for o in p.overrides],
        "parameters": _flatten(p.parameters.model_dump()),
    }


def _flatten(node: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
        return out
    return {prefix: node}


def diff_policies(old: Policy, new: Policy) -> list[dict[str, str]]:
    """Plain-language list of what changes from ``old`` to ``new``."""
    changes: list[dict[str, str]] = []
    if old.effective_from != new.effective_from:
        changes.append({"kind": "meta", "text": f"Takes effect from {new.effective_from.isoformat()} (version {new.version})."})

    old_p, new_p = _flatten(old.parameters.model_dump()), _flatten(new.parameters.model_dump())
    for path in parameter_catalogue():
        if old_p.get(path) != new_p.get(path):
            changes.append({"kind": "parameter", "text": f"Parameter {path}: {old_p.get(path)} -> {new_p.get(path)}"})

    old_rules = {r.id: r for r in old.rules}
    new_rules = {r.id: r for r in new.rules}
    for r in new.rules:
        if r.id not in old_rules:
            pos = new.rules.index(r)
            where = f"before {new.rules[pos + 1].clause}" if pos + 1 < len(new.rules) else "at the end"
            changes.append({"kind": "rule_added", "text": f"New rule {where}: {describe_rule(r)}"})
        elif r.model_dump() != old_rules[r.id].model_dump():
            changes.append({
                "kind": "rule_changed",
                "text": f"Rule {r.clause} changes from \"{describe_rule(old_rules[r.id])}\" to \"{describe_rule(r)}\"",
            })
    for r in old.rules:
        if r.id not in new_rules:
            changes.append({"kind": "rule_removed", "text": f"Rule removed: {describe_rule(r)}"})
    kept_old = [r.id for r in old.rules if r.id in new_rules]
    kept_new = [r.id for r in new.rules if r.id in old_rules]
    if kept_old != kept_new:
        order = " > ".join(new_rules[i].clause for i in kept_new)
        changes.append({"kind": "rule_order", "text": f"Rule order changes to: {order}"})

    old_ov = {o.id: o for o in old.overrides}
    new_ov = {o.id: o for o in new.overrides}
    for o in new.overrides:
        if o.id not in old_ov:
            changes.append({"kind": "override_added", "text": f"New conditional setting: {describe_override(o)}"})
        elif o.model_dump() != old_ov[o.id].model_dump():
            changes.append({"kind": "override_changed", "text": f"Conditional setting changes to: {describe_override(o)}"})
    for o in old.overrides:
        if o.id not in new_ov:
            changes.append({"kind": "override_removed", "text": f"Conditional setting removed: {describe_override(o)}"})
    return changes
