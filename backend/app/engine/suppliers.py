"""Preferred supplier list (policy section 6).

Ranking score (weights live in the policy, ``parameters.preferred_list``)::

    score = w_freq * (POs for the item in the window / max POs of any listed supplier)
          + w_rec  * recency bucket of the last PO (1 = within 90 days, 0.5 = within 365, else 0)
          + w_con  * (1 if the supplier has held a contract for the item, else 0)
          + w_perf * performance

    performance = on_time_weight * on_time_pct/100 + quality_weight * quality_rating/5

Ties are broken by performance, then supplier id, so the order is stable.
Every component is returned with the entry so the buyer can see *why*.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

from .policy import Parameters
from .types import Contract, MasterData, Supplier


@dataclass
class PreferredEntry:
    rank: int
    supplier_id: str
    supplier_name: str
    score: float
    po_count: int
    last_po_date: date | None
    recency: float
    frequency: float
    holds_contract: bool
    performance: float
    on_time_pct: float
    quality_rating: float
    reference_price: float | None
    reference_source: str  # e.g. "PO 7300244 (2026-10-19)" or "contract RC-2026-002"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["last_po_date"] = self.last_po_date.isoformat() if self.last_po_date else None
        return d


def performance(supplier: Supplier, params: Parameters) -> float:
    p = params.preferred_list
    return p.on_time_weight * supplier.on_time_pct / 100 + p.quality_weight * supplier.quality_rating / 5


def _recency(days: int | None, params: Parameters) -> float:
    if days is None:
        return 0.0
    for bucket in sorted(params.preferred_list.recency_buckets, key=lambda b: b.max_days):
        if days <= bucket.max_days:
            return bucket.score
    return 0.0


def is_allowed(supplier: Supplier | None, params: Parameters) -> bool:
    """Policy 5.7: only allowed (Active) suppliers may get a PO or be suggested."""
    e = params.eligibility
    allowed = {s.lower() for s in e.allowed_supplier_statuses}
    return (
        supplier is not None
        and supplier.status.lower() in allowed
        and supplier.quality_rating >= e.min_quality_rating
        and supplier.on_time_pct >= e.min_on_time_pct
    )


def build_preferred_list(md: MasterData, item_code: str, as_of: date, params: Parameters) -> list[PreferredEntry]:
    pl = params.preferred_list
    window_start = as_of - timedelta(days=pl.po_lookback_days)
    pos = [p for p in md.pos_for_item(item_code) if window_start <= p.po_date <= as_of]

    by_supplier: dict[str, list] = {}
    for po in pos:
        by_supplier.setdefault(po.supplier_id, []).append(po)

    contracts_by_supplier: dict[str, list[Contract]] = {}
    if pl.include_contract_holders:
        for c in md.contracts_for_item(item_code):
            if c.start_date <= as_of:
                contracts_by_supplier.setdefault(c.supplier_id, []).append(c)

    candidate_ids = set(by_supplier) | set(contracts_by_supplier)
    candidates = [md.suppliers[s] for s in candidate_ids if is_allowed(md.suppliers.get(s), params)]
    if not candidates:
        return []
    max_count = max((len(by_supplier.get(s.supplier_id, [])) for s in candidates), default=0) or 1

    w = pl.weights
    entries: list[PreferredEntry] = []
    for sup in candidates:
        sup_pos = by_supplier.get(sup.supplier_id, [])
        last = sup_pos[-1] if sup_pos else None  # pos are sorted oldest-first
        days = (as_of - last.po_date).days if last else None
        frequency = len(sup_pos) / max_count
        recency = _recency(days, params)
        contracts = sorted(contracts_by_supplier.get(sup.supplier_id, []), key=lambda c: (c.start_date, c.contract_id))
        holds = bool(contracts)
        perf = performance(sup, params)
        score = w.frequency * frequency + w.recency * recency + w.contract * holds + w.performance * perf

        if last:
            ref_price, ref_src = last.unit_price, f"PO {last.po_number} ({last.po_date.isoformat()})"
        elif contracts:
            latest = contracts[-1]
            ref_price, ref_src = latest.rate_for(item_code), f"contract {latest.contract_id}"
        else:
            ref_price, ref_src = None, "none"

        entries.append(
            PreferredEntry(
                rank=0,
                supplier_id=sup.supplier_id,
                supplier_name=sup.name,
                score=round(score, 4),
                po_count=len(sup_pos),
                last_po_date=last.po_date if last else None,
                recency=recency,
                frequency=round(frequency, 4),
                holds_contract=holds,
                performance=round(perf, 4),
                on_time_pct=sup.on_time_pct,
                quality_rating=sup.quality_rating,
                reference_price=ref_price,
                reference_source=ref_src,
            )
        )
    entries.sort(key=lambda e: (-e.score, -e.performance, e.supplier_id))
    for i, e in enumerate(entries, start=1):
        e.rank = i
    return entries


def suggest_suppliers(
    md: MasterData, category: str, preferred: list[PreferredEntry], count: int, params: Parameters
) -> list[str]:
    """Policy 6.4: preferred list first, then top up from the category by delivery + quality."""
    chosen = [e.supplier_id for e in preferred[:count]]
    if len(chosen) < count and params.suggestions.top_up_from_category:
        pool = [
            s
            for s in md.suppliers.values()
            if s.supplier_id not in chosen and is_allowed(s, params) and category in s.categories
        ]
        pool.sort(key=lambda s: (-performance(s, params), s.supplier_id))
        chosen += [s.supplier_id for s in pool[: count - len(chosen)]]
    return chosen
