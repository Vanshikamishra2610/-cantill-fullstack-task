"""The decision engine: PR lines in, one decision per line out.

Steps (each driven by the policy version in force on the relevant date):

1. validate lines           -> NOT_PROCESSED for lines that cannot be decided
2. match items              (1.1 / 1.2)
3. club lines               (2.2 / 2.3)
4. value each requirement   (3.1, using contracts 4.1-4.3)
5. evaluate the rule table  (section 5, first rule that fits)
6. pick supplier / suggest  (section 6)
7. consolidate POs          (section 7)

The engine is pure: it takes ``MasterData`` + a ``PolicyBook`` and returns a
``BatchResult``. Persisting, approving and exporting live in the service layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .contracts import CeilingLedger, ContractOption, contract_options
from .matcher import ItemMatcher, MatchResult
from .policy import Policy, PolicyBook, Rule
from .suppliers import PreferredEntry, build_preferred_list, is_allowed, suggest_suppliers
from .types import MasterData, PRLine

NEW = "NEW"
NOT_PROCESSED = "NOT_PROCESSED"


def fmt_inr(value: float | None) -> str:
    """Indian digit grouping: 1234567.8 -> 12,34,568."""
    if value is None:
        return "-"
    n = int(round(value))
    sign, s = ("-" if n < 0 else ""), str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join(groups + [tail])


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------
@dataclass
class LineResult:
    line: PRLine
    match: MatchResult | None
    resolved_item_code: str | None
    policy_version: str = ""
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    requirement_id: str | None = None

    @property
    def processable(self) -> bool:
        return not self.errors


@dataclass
class Requirement:
    id: str
    item_code: str  # NEW for new items
    plant: str
    lines: list[LineResult]
    policy: Policy
    item_description: str = ""
    category: str = ""
    item_type: str = ""
    uom: str = ""
    # filled by valuation / decision
    quantity: float = 0.0
    valuation_price: float = 0.0
    price_source: str = ""
    value: float = 0.0
    facts: dict[str, Any] = field(default_factory=dict)
    rule: Rule | None = None
    decision: str = ""
    route: str = ""
    supplier_id: str | None = None
    supplier_name: str | None = None
    unit_price: float | None = None
    contract_id: str | None = None
    reference: str | None = None  # contract or PO relied on
    suggestions: list[str] = field(default_factory=list)
    preferred: list[PreferredEntry] = field(default_factory=list)
    contract_review: list[dict] = field(default_factory=list)
    reason: str = ""
    notes: list[str] = field(default_factory=list)
    po_id: str | None = None

    @property
    def first(self) -> PRLine:
        return self.lines[0].line

    @property
    def created_at(self) -> datetime:
        return self.first.created_at  # type: ignore[return-value]

    @property
    def requirement_date(self) -> date:
        return self.created_at.date()

    @property
    def is_new_item(self) -> bool:
        return self.item_code == NEW

    @property
    def is_emergency(self) -> bool:
        return any(lr.line.is_emergency for lr in self.lines)


@dataclass
class PurchaseOrderDraft:
    po_id: str
    supplier_id: str
    plant: str
    route: str
    first_created_at: datetime
    requirements: list[Requirement] = field(default_factory=list)

    @property
    def value(self) -> float:
        return round(sum(r.value for r in self.requirements), 2)


@dataclass
class BatchResult:
    lines: list[LineResult]
    requirements: list[Requirement]
    purchase_orders: list[PurchaseOrderDraft]
    versions_used: list[str]

    def requirement_for(self, lr: LineResult) -> Requirement | None:
        return self._req_index.get(lr.requirement_id) if lr.requirement_id else None

    def __post_init__(self) -> None:
        self._req_index = {r.id: r for r in self.requirements}

    def export_rows(self) -> list[dict[str, Any]]:
        """One row per input line, in input order, in the brief's section 4 format."""
        rows = []
        for lr in self.lines:
            req = self.requirement_for(lr)
            line = lr.line
            if req is None:
                rows.append(
                    {
                        "pr_no": line.pr_no,
                        "pr_line": line.pr_line,
                        "resolved_item_code": lr.resolved_item_code or "",
                        "group_id": "",
                        "po_id": "",
                        "decision": NOT_PROCESSED,
                        "route": NOT_PROCESSED,
                        "supplier_id": "",
                        "unit_price": "",
                        "group_value_inr": "",
                        "suggested_supplier_ids": "",
                        "policy_version": lr.policy_version,
                        "reason": "Not processed: " + "; ".join(lr.errors),
                    }
                )
                continue
            rows.append(
                {
                    "pr_no": line.pr_no,
                    "pr_line": line.pr_line,
                    "resolved_item_code": req.item_code,
                    "group_id": req.id,
                    "po_id": req.po_id or "",
                    "decision": req.decision,
                    "route": req.route,
                    "supplier_id": req.supplier_id or "",
                    "unit_price": "" if req.unit_price is None else round(req.unit_price, 2),
                    "group_value_inr": round(req.value, 2),
                    "suggested_supplier_ids": ";".join(req.suggestions),
                    "policy_version": req.policy.version,
                    "reason": req.reason,
                }
            )
        return rows


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------
class DecisionEngine:
    def __init__(self, md: MasterData, book: PolicyBook):
        self.md = md
        self.book = book
        self.matcher = ItemMatcher(md.items)

    # -- step 1 + 2 ---------------------------------------------------------
    def _context(self, item_code: str | None, plant: str, line: PRLine | None = None) -> dict[str, Any]:
        item = self.md.items.get(item_code or "")
        return {
            "plant": plant,
            "item_code": item_code or NEW,
            "category": item.category if item else "",
            "item_type": item.item_type if item else "",
            "urgency": line.urgency if line else "",
            "is_emergency": line.is_emergency if line else False,
            "department": line.department if line else "",
        }

    def _prepare_line(self, line: PRLine, seen: set[tuple[str, int]]) -> LineResult:
        errors = list(line.raw_errors)
        policy = self.book.for_date(line.created_at.date() if line.created_at else None)
        if line.key in seen:
            errors.append(f"duplicate of an earlier row with the same PR number and line ({line.pr_no}/{line.pr_line})")
        seen.add(line.key)
        if line.created_at is None and not any("created_at" in e for e in errors):
            errors.append("creation time is missing")
        if line.plant not in self.md.plants:
            errors.append(f"plant '{line.plant or '(blank)'}' is not a Corvo plant ({', '.join(sorted(self.md.plants))})")
        if line.quantity is None:
            if not any("quantity" in e for e in errors):
                errors.append("quantity is missing")
        elif line.quantity <= 0:
            errors.append(f"quantity must be greater than zero (got {line.quantity:g})")
        if line.need_by_date is None and not any("need_by" in e for e in errors):
            errors.append("need-by date is missing")

        params = policy.params_for(self._context(line.item_code, line.plant, line))
        m = params.matching
        match = self.matcher.match(line.item_code, line.description, m.min_score, m.ambiguity_margin, m.vague_score)
        notes: list[str] = []
        resolved: str | None
        if match.status == "matched":
            resolved = match.item_code
            item = self.md.items[resolved]
            if line.item_code and line.item_code.strip().upper() != resolved:
                notes.append(f"item code '{line.item_code}' not in item master; matched on description")
            if match.method == "description":
                notes.append(f"'{line.description}' matched to {resolved} ({item.description}), score {match.score:.2f}")
            if line.uom and line.uom.strip().upper() != item.uom.upper():
                errors.append(f"unit '{line.uom}' does not match the item master unit '{item.uom}' for {resolved}")
        elif match.status == "ambiguous":
            resolved = None
            options = ", ".join(f"{c.item_code} {c.description}" for c in match.candidates)
            errors.append(f"description '{line.description}' is ambiguous between {options}; requester must confirm the item")
        else:
            resolved = NEW
        return LineResult(
            line=line, match=match, resolved_item_code=resolved, policy_version=policy.version, errors=errors, notes=notes
        )

    def _flag_duplicates(self, lines: list[LineResult]) -> None:
        """Hold suspected double submissions (system safeguard, see DuplicateParams)."""
        kept: list[LineResult] = []
        for lr in sorted((l for l in lines if l.processable), key=lambda l: l.line.sort_key):
            line = lr.line
            policy = self.book.for_date(line.created_at.date())
            p = policy.params_for(self._context(lr.resolved_item_code, line.plant, line)).duplicates
            original = None
            if p.enabled and lr.resolved_item_code != NEW:
                for prev in kept:
                    pl = prev.line
                    if (
                        pl.pr_no != line.pr_no
                        and pl.requester.lower() == line.requester.lower()
                        and pl.plant == line.plant
                        and prev.resolved_item_code == lr.resolved_item_code
                        and pl.quantity == line.quantity
                        and pl.need_by_date == line.need_by_date
                        and line.created_at - pl.created_at <= timedelta(minutes=p.window_minutes)
                    ):
                        original = prev
                        break
            if original is None:
                kept.append(lr)
            else:
                mins = int((line.created_at - original.line.created_at).total_seconds() // 60)
                lr.errors.append(
                    f"suspected double submission of {original.line.pr_no}/{original.line.pr_line} "
                    f"(same requester, plant, item, quantity and need-by date, {mins} min apart); "
                    "held for the requester to confirm"
                )

    # -- step 3 -------------------------------------------------------------
    def _club(self, lines: list[LineResult]) -> list[Requirement]:
        requirements: list[Requirement] = []
        open_reqs: dict[tuple[str, str], list[Requirement]] = {}
        for lr in sorted((l for l in lines if l.processable), key=lambda l: l.line.sort_key):
            line, code = lr.line, lr.resolved_item_code or NEW
            joined = None
            if code != NEW:
                for req in open_reqs.get((code, line.plant), []):
                    p = req.policy.params_for(self._context(code, line.plant, req.first)).clubbing
                    if not p.enabled:
                        continue
                    within_creation = (line.created_at.date() - req.requirement_date).days <= p.creation_window_days
                    within_need_by = abs((line.need_by_date - req.first.need_by_date).days) <= p.need_by_window_days
                    if within_creation and within_need_by:
                        joined = req
                        break  # requirements are kept in creation order -> earliest wins
            else:
                policy = self.book.for_date(line.created_at.date())
                if not policy.params_for(self._context(None, line.plant, line)).clubbing.exclude_new_items:
                    # New items are clubbed only on identical normalised description.
                    for req in open_reqs.get((NEW + ":" + line.description.strip().lower(), line.plant), []):
                        joined = req
                        break
            if joined is None:
                policy = self.book.for_date(line.created_at.date())
                item = self.md.items.get(code)
                joined = Requirement(
                    id=f"REQ-{len(requirements) + 1:04d}",
                    item_code=code,
                    plant=line.plant,
                    lines=[],
                    policy=policy,
                    item_description=item.description if item else line.description,
                    category=item.category if item else "",
                    item_type=item.item_type if item else "",
                    uom=item.uom if item else line.uom,
                )
                requirements.append(joined)
                key = (code, line.plant) if code != NEW else (NEW + ":" + line.description.strip().lower(), line.plant)
                open_reqs.setdefault(key, []).append(joined)
            joined.lines.append(lr)
            lr.requirement_id = joined.id
        return requirements

    # -- step 4 -------------------------------------------------------------
    def _value(self, req: Requirement, ledger: CeilingLedger) -> list[ContractOption]:
        params = req.policy.params_for(self._context(req.item_code, req.plant, req.first))
        req.quantity = sum(lr.line.quantity for lr in req.lines)
        d = req.requirement_date
        if req.is_new_item:
            estimates = [lr.line.estimated_unit_price for lr in req.lines if lr.line.estimated_unit_price]
            req.valuation_price = estimates[0] if estimates else 0.0
            req.price_source = "requester's estimate" if estimates else "no estimate given (valued at 0)"
            if not estimates:
                req.notes.append("new item has no estimated price; value could not be determined")
            req.value = round(req.quantity * req.valuation_price, 2)
            return []

        options = contract_options(self.md, ledger, req.item_code, req.plant, req.quantity, d, params)
        usable = next((o for o in options if o.usable), None)
        req.contract_review = [
            {
                "contract_id": o.contract_id,
                "supplier_id": o.contract.supplier_id,
                "rate": o.rate,
                "value_at_rate": o.value_at_rate,
                "ceiling": o.contract.value_cap,
                "remaining_before": round(o.remaining_before, 2),
                "usable": o.usable,
            }
            for o in options
        ]
        if usable:
            req.valuation_price, req.price_source = usable.rate, f"contract {usable.contract_id} rate (3.1a)"
        elif options:
            low = options[0]
            req.valuation_price = low.rate
            req.price_source = f"lowest applicable contract rate, {low.contract_id}, all contracts out of ceiling (3.1a)"
        else:
            start = d - timedelta(days=params.valuation.price_lookback_days)
            recent = [p for p in self.md.pos_for_item(req.item_code) if start <= p.po_date <= d]
            if recent:
                last = recent[-1]
                req.valuation_price = last.unit_price
                req.price_source = f"last PO {last.po_number} on {last.po_date.isoformat()} (3.1b)"
            else:
                req.valuation_price = self.md.items[req.item_code].standard_price
                req.price_source = "item master standard price (3.1c)"
        req.value = round(req.quantity * req.valuation_price, 2)
        return options

    def _facts(self, req: Requirement, options: list[ContractOption]) -> dict[str, Any]:
        params = req.policy.params_for(self._context(req.item_code, req.plant, req.first))
        d = req.requirement_date
        bought = False
        if not req.is_new_item:
            start = d - timedelta(days=params.history.purchase_lookback_days)
            bought = any(
                start <= p.po_date <= d and is_allowed(self.md.suppliers.get(p.supplier_id), params)
                for p in self.md.pos_for_item(req.item_code)
            )
        earliest_need = min(lr.line.need_by_date for lr in req.lines)
        return {
            "item_code": req.item_code,
            "item_type": req.item_type,
            "category": req.category,
            "plant": req.plant,
            "value": req.value,
            "quantity": req.quantity,
            "uom": req.uom,
            "urgency": "Emergency" if req.is_emergency else "Normal",
            "is_emergency": req.is_emergency,
            "is_new_item": req.is_new_item,
            "contract_available": any(o.usable for o in options),
            "contract_applicable": bool(options),
            "bought_recently": bought,
            "line_count": len(req.lines),
            "lead_time_days": (earliest_need - d).days,
            "preferred_supplier_count": len(req.preferred),
            "department": req.first.department,
            "requirement_date": d.isoformat(),
        }

    # -- step 5 + 6 ---------------------------------------------------------
    def _decide(self, req: Requirement, ledger: CeilingLedger) -> None:
        params = req.policy.params_for(self._context(req.item_code, req.plant, req.first))
        options = self._value(req, ledger)
        if not req.is_new_item:
            req.preferred = build_preferred_list(self.md, req.item_code, req.requirement_date, params)
        req.facts = self._facts(req, options)
        usable = next((o for o in options if o.usable), None)

        skipped: list[str] = []
        for rule in req.policy.rules:
            if not rule.enabled or not rule.when.evaluate(req.facts):
                continue
            mode = rule.outcome.supplier
            if mode == "contract" and usable is None:
                skipped.append(f"{rule.clause} needs a usable contract")
                continue
            if mode == "preferred" and not req.preferred:
                skipped.append(f"{rule.clause} needs a preferred supplier")
                continue
            req.rule = rule
            break

        if req.rule is None:
            # The rule table should always be exhaustive; if an amendment leaves a
            # gap we fail safe to quotes rather than placing a PO.
            req.decision, req.route = "SOURCING_REQUEST", "RFQ"
            req.suggestions = suggest_suppliers(self.md, req.category, req.preferred, 3, params) if not req.is_new_item else []
            req.reason = f"Policy v{req.policy.version}: no rule fitted; sent to RFQ as a safe default."
            return

        outcome = req.rule.outcome
        req.decision, req.route = outcome.decision, outcome.route
        if outcome.supplier == "contract":
            req.contract_id = usable.contract_id
            req.supplier_id = usable.contract.supplier_id
            req.unit_price = usable.rate
            req.reference = usable.contract_id
            ledger.consume(usable.contract_id, req.value)
        elif outcome.supplier == "preferred":
            top = req.preferred[0]
            req.supplier_id, req.unit_price, req.reference = top.supplier_id, top.reference_price, top.reference_source
        elif outcome.supplier == "suggest" and not req.is_new_item:
            req.suggestions = suggest_suppliers(self.md, req.category, req.preferred, outcome.suggest_count, params)
        if req.supplier_id:
            req.supplier_name = self.md.suppliers[req.supplier_id].name
        req.reason = self._reason(req, usable, options, skipped)

    def _reason(self, req: Requirement, usable: ContractOption | None, options: list[ContractOption], skipped: list[str]) -> str:
        rule = req.rule
        parts = [f"Policy v{req.policy.version} clause {rule.clause} ({rule.title})."]
        if len(req.lines) > 1:
            parts.append(f"Clubbed {len(req.lines)} PR lines (2.2).")
        parts.append(f"Value {fmt_inr(req.value)} = {req.quantity:g} x {req.valuation_price:g} from {req.price_source}.")
        if req.route == "CONTRACT_PO" and usable:
            remaining = usable.remaining_before - req.value
            parts.append(
                f"Contract {usable.contract_id} with {req.supplier_id} at {usable.rate:g}; ceiling left after this PO {fmt_inr(remaining)}."
            )
        unusable = [o for o in options if not o.usable]
        if unusable:
            parts.append(
                "Not enough ceiling on " + ", ".join(f"{o.contract_id} (left {fmt_inr(o.remaining_before)})" for o in unusable) + " (4.2)."
            )
        if req.decision == "PO" and req.route != "CONTRACT_PO" and req.preferred:
            top = req.preferred[0]
            parts.append(f"1st preferred supplier {top.supplier_id} at reference price {top.reference_price:g} from {top.reference_source} (6.3).")
        if req.suggestions:
            parts.append("Suggested suppliers: " + ", ".join(req.suggestions) + " (6.4).")
        if req.decision == "SOURCING_REQUEST" and not req.facts.get("bought_recently") and not req.is_new_item and req.route in ("RFQ", "RFP"):
            parts.append("Not bought from an Active supplier in the look-back window.")
        if rule.outcome.note:
            parts.append(rule.outcome.note)
        if skipped:
            parts.append("Skipped: " + "; ".join(skipped) + ".")
        return " ".join(parts)

    # -- step 7 -------------------------------------------------------------
    def _consolidate(self, requirements: list[Requirement]) -> list[PurchaseOrderDraft]:
        pos: list[PurchaseOrderDraft] = []
        for req in sorted((r for r in requirements if r.decision == "PO"), key=lambda r: r.first.sort_key):
            p = req.policy.params_for(self._context(req.item_code, req.plant, req.first)).consolidation
            target = None
            if p.enabled:
                for po in pos:
                    same = all(
                        {"supplier": po.supplier_id == req.supplier_id, "plant": po.plant == req.plant, "route": po.route == req.route}[k]
                        for k in p.match_on
                    )
                    if same and req.created_at - po.first_created_at <= timedelta(hours=p.window_hours):
                        target = po
                        break
            if target is None:
                target = PurchaseOrderDraft(
                    po_id=f"PO-{len(pos) + 1:04d}",
                    supplier_id=req.supplier_id,
                    plant=req.plant,
                    route=req.route,
                    first_created_at=req.created_at,
                )
                pos.append(target)
            target.requirements.append(req)
            req.po_id = target.po_id
            if len(target.requirements) > 1:
                req.reason += f" Consolidated into {target.po_id} with {target.requirements[0].id} (7.1)."
        return pos

    # -- public -------------------------------------------------------------
    def process(self, lines: list[PRLine]) -> BatchResult:
        seen: set[tuple[str, int]] = set()
        prepared = [self._prepare_line(line, seen) for line in lines]
        self._flag_duplicates(prepared)
        requirements = self._club(prepared)
        ledger = CeilingLedger(self.md.contracts)
        for req in sorted(requirements, key=lambda r: r.first.sort_key):
            self._decide(req, ledger)
        pos = self._consolidate(requirements)
        versions = sorted({r.policy.version for r in requirements})
        return BatchResult(lines=prepared, requirements=requirements, purchase_orders=pos, versions_used=versions)
