"""Policy-as-data: the schema every policy version must follow.

A policy version is a JSON document with three parts:

* ``parameters`` - every number the engine uses (windows, look-backs, ranking
  weights, consolidation window...). Nothing numeric is hard-coded in the engine.
* ``overrides`` - conditional parameter changes, e.g. "at CHK1 the clubbing
  window is 21 days". They are applied on top of ``parameters`` for the lines /
  requirements whose context matches.
* ``rules`` - the ordered decision table (policy section 5). The first enabled
  rule whose ``when`` condition holds decides the route. Conditions are a small
  boolean language over the requirement *facts* listed in ``FACTS``.

Adding a new kind of rule (plant-only, category-only, value band, urgency,
lead time...) therefore needs a new JSON entry, not new code.
"""
from __future__ import annotations

import copy
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Facts a rule condition may test. Kept here so the policy reader (LLM), the
# UI and the validator all share one catalogue.
# ---------------------------------------------------------------------------
FACTS: dict[str, str] = {
    "item_code": "Matched item code (NEW for new items)",
    "item_type": "Item type from the item master: Material, Service or Capex",
    "category": "Item category from the item master, e.g. 'Safety & PPE'",
    "plant": "Plant code: PUN1 or CHK1",
    "value": "Requirement value in INR (policy section 3)",
    "quantity": "Total quantity of the requirement",
    "uom": "Unit of measure",
    "urgency": "'Emergency' if any line is marked Emergency, else 'Normal'",
    "is_emergency": "true if any line of the requirement is marked Emergency",
    "is_new_item": "true if the item matched nothing in the item master",
    "contract_available": "true if a rate contract applies and has enough ceiling left (4.1-4.3)",
    "contract_applicable": "true if any rate contract applies, ignoring ceiling (4.1)",
    "bought_recently": "true if bought from an allowed (Active) supplier within history.purchase_lookback_days",
    "line_count": "Number of PR lines clubbed into the requirement",
    "lead_time_days": "Days between the requirement date and the earliest need-by date",
    "preferred_supplier_count": "Number of suppliers on the item's preferred list",
    "department": "Department of the first PR line",
    "requirement_date": "Requirement date (ISO yyyy-mm-dd)",
}

# Facts available while clubbing / matching, i.e. before a requirement is valued.
CONTEXT_FACTS = {"plant", "category", "item_type", "item_code", "urgency", "is_emergency", "department"}

Op = Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "contains", "exists"]


class Condition(BaseModel):
    """Either a leaf ``{field, op, value}`` or a group ``{all|any|not: ...}``."""

    model_config = ConfigDict(populate_by_name=True)

    field: str | None = None
    op: Op | None = None
    value: Any = None
    all: list["Condition"] | None = None
    any: list["Condition"] | None = None
    not_: "Condition | None" = Field(default=None, alias="not")

    @model_validator(mode="after")
    def _check_shape(self) -> "Condition":
        groups = [g for g in (self.all, self.any, self.not_) if g is not None]
        if self.field is not None:
            if groups:
                raise ValueError("a condition is either a leaf (field/op) or a group, not both")
            if self.field not in FACTS:
                raise ValueError(f"unknown fact '{self.field}'. Known facts: {', '.join(FACTS)}")
            if self.op is None:
                raise ValueError(f"condition on '{self.field}' needs an op")
        elif len(groups) != 1:
            raise ValueError("a group condition needs exactly one of all / any / not")
        return self

    def evaluate(self, facts: dict[str, Any]) -> bool:
        if self.all is not None:
            return all(c.evaluate(facts) for c in self.all)
        if self.any is not None:
            return any(c.evaluate(facts) for c in self.any)
        if self.not_ is not None:
            return not self.not_.evaluate(facts)
        return _compare(facts.get(self.field), self.op, self.value)

    def fields_used(self) -> set[str]:
        if self.field is not None:
            return {self.field}
        children = self.all or self.any or ([self.not_] if self.not_ else [])
        return set().union(*(c.fields_used() for c in children)) if children else set()


def _norm(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "exists":
        present = actual is not None and actual != ""
        return present if expected in (None, True) else not present
    if actual is None:
        return False
    a, e = _norm(actual), expected
    if op in ("in", "not_in"):
        options = [_norm(x) for x in (e if isinstance(e, list) else [e])]
        return (a in options) if op == "in" else (a not in options)
    e = _norm(e)
    if op == "contains":
        return isinstance(a, str) and str(e) in a
    if op == "eq":
        return a == e
    if op == "ne":
        return a != e
    try:
        a_num, e_num = float(a), float(e)
    except (TypeError, ValueError):
        a_num, e_num = a, e  # fall back to string ordering (e.g. ISO dates)
    return {
        "lt": a_num < e_num,
        "lte": a_num <= e_num,
        "gt": a_num > e_num,
        "gte": a_num >= e_num,
    }[op]


Decision = Literal["PO", "SOURCING_REQUEST", "NOT_PROCESSED"]
SupplierMode = Literal["contract", "preferred", "suggest", "none"]


class Outcome(BaseModel):
    decision: Decision
    route: str
    supplier: SupplierMode = "none"
    suggest_count: int = 0
    note: str | None = None

    @field_validator("route")
    @classmethod
    def _route_upper(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _consistent(self) -> "Outcome":
        if self.decision == "PO" and self.supplier not in ("contract", "preferred"):
            raise ValueError("a PO outcome needs supplier 'contract' or 'preferred'")
        if self.decision != "PO" and self.supplier in ("contract", "preferred"):
            raise ValueError("only PO outcomes can name a contract / preferred supplier")
        return self


class Rule(BaseModel):
    id: str
    clause: str
    title: str
    when: Condition
    outcome: Outcome
    enabled: bool = True


class Override(BaseModel):
    """Conditional parameter change, e.g. a plant-specific clubbing window."""

    id: str
    description: str
    when: Condition
    set: dict[str, Any]

    @model_validator(mode="after")
    def _context_only(self) -> "Override":
        bad = self.when.fields_used() - CONTEXT_FACTS
        if bad:
            raise ValueError(
                f"override '{self.id}' may only test {sorted(CONTEXT_FACTS)}, not {sorted(bad)}"
            )
        return self


# ----------------------------- parameters ---------------------------------
class MatchingParams(BaseModel):
    min_score: float = 0.6
    ambiguity_margin: float = 0.05
    vague_score: float = 0.4


class ClubbingParams(BaseModel):
    enabled: bool = True
    creation_window_days: int = 14
    need_by_window_days: int = 7
    exclude_new_items: bool = True


class DuplicateParams(BaseModel):
    """Not in policy v5.0 - a system safeguard (see DECISIONS.md).

    A line is held as a suspected double submission when an earlier line in a
    *different* PR has the same requester, plant, item, quantity and need-by
    date and was created within ``window_minutes``.
    """

    enabled: bool = True
    window_minutes: int = 60


class ValuationParams(BaseModel):
    price_lookback_days: int = 365


class HistoryParams(BaseModel):
    purchase_lookback_days: int = 365


class ContractParams(BaseModel):
    require_active_supplier: bool = True
    ceiling_tolerance_pct: float = 0.0


class RecencyBucket(BaseModel):
    max_days: int
    score: float


class RankingWeights(BaseModel):
    frequency: float = 0.35
    recency: float = 0.25
    contract: float = 0.20
    performance: float = 0.20


class PreferredListParams(BaseModel):
    po_lookback_days: int = 548
    include_contract_holders: bool = True
    weights: RankingWeights = RankingWeights()
    recency_buckets: list[RecencyBucket] = [
        RecencyBucket(max_days=90, score=1.0),
        RecencyBucket(max_days=365, score=0.5),
    ]
    on_time_weight: float = 0.5
    quality_weight: float = 0.5


class SuggestionParams(BaseModel):
    top_up_from_category: bool = True


class ConsolidationParams(BaseModel):
    enabled: bool = True
    window_hours: float = 24
    match_on: list[Literal["supplier", "plant", "route"]] = ["supplier", "plant", "route"]


class EligibilityParams(BaseModel):
    allowed_supplier_statuses: list[str] = ["Active"]
    min_quality_rating: float = 0.0
    min_on_time_pct: float = 0.0


class Parameters(BaseModel):
    matching: MatchingParams = MatchingParams()
    clubbing: ClubbingParams = ClubbingParams()
    duplicates: DuplicateParams = DuplicateParams()
    valuation: ValuationParams = ValuationParams()
    history: HistoryParams = HistoryParams()
    contracts: ContractParams = ContractParams()
    preferred_list: PreferredListParams = PreferredListParams()
    suggestions: SuggestionParams = SuggestionParams()
    consolidation: ConsolidationParams = ConsolidationParams()
    eligibility: EligibilityParams = EligibilityParams()


class Policy(BaseModel):
    version: str
    effective_from: date
    title: str = "Corvo Procurement Policy"
    summary: str = ""
    parameters: Parameters = Parameters()
    overrides: list[Override] = []
    rules: list[Rule]

    @model_validator(mode="after")
    def _unique_ids(self) -> "Policy":
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")
        for ov in self.overrides:
            for path in ov.set:
                _validate_param_path(path)
        return self

    # ---- parameter lookup with overrides -----------------------------------
    def params_for(self, context: dict[str, Any] | None = None) -> Parameters:
        """Parameters after applying every override whose condition matches ``context``."""
        if not self.overrides or context is None:
            return self.parameters
        cache_key = tuple(sorted((k, str(context.get(k))) for k in CONTEXT_FACTS))
        cache = self.__dict__.setdefault("_param_cache", {})
        if cache_key not in cache:
            data = self.parameters.model_dump()
            for ov in self.overrides:
                if ov.when.evaluate(context):
                    for path, value in ov.set.items():
                        _set_path(data, path, value)
            cache[cache_key] = Parameters.model_validate(data)
        return cache[cache_key]


def _validate_param_path(path: str) -> None:
    data = Parameters().model_dump()
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"unknown parameter '{path}'")
        node = node[part]


def _set_path(data: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = copy.deepcopy(value)


def parameter_catalogue() -> dict[str, Any]:
    """Flat ``{dotted.path: default}`` list, used by the UI and the policy reader."""
    out: dict[str, Any] = {}

    def walk(prefix: str, node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        else:
            out[prefix] = node

    walk("", Parameters().model_dump())
    return out


class PolicyBook:
    """All approved versions; picks the one in force on a given date."""

    def __init__(self, versions: list[Policy]):
        if not versions:
            raise ValueError("at least one policy version is required")
        # stable sort: for equal effective dates the later-approved version (later in the input) wins
        self.versions = sorted(versions, key=lambda p: p.effective_from)

    def for_date(self, d: date | None) -> Policy:
        if d is None:
            return self.versions[-1]
        in_force = [p for p in self.versions if p.effective_from <= d]
        # Before the first effective date we fall back to the earliest version
        # (recorded in DECISIONS.md) rather than refusing to decide.
        return in_force[-1] if in_force else self.versions[0]
