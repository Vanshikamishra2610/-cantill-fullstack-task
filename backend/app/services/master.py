"""Master data access: seed from the kit, load into engine objects."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import kit_loader
from ..config import KIT_DIR, POLICY_SEED
from ..engine.policy import Policy
from ..engine.types import Contract, ContractLine, Item, MasterData, PurchaseOrder, Supplier
from ..models import (
    AuditRow,
    ContractLineRow,
    ContractRow,
    ItemRow,
    PlantRow,
    PoHistoryRow,
    PolicyVersionRow,
    SupplierRow,
)

log = logging.getLogger(__name__)


def audit(db: Session, actor: str, action: str, entity: str, entity_id: str, comment: str = "", **details: Any) -> None:
    db.add(AuditRow(actor=actor or "anonymous", action=action, entity=entity, entity_id=str(entity_id), comment=comment, details=details))


def contract_to_row(c: Contract, added_by: str = "system") -> ContractRow:
    row = ContractRow(
        contract_id=c.contract_id, supplier_id=c.supplier_id, supplier_name=c.supplier_name,
        start_date=c.start_date, end_date=c.end_date, value_cap=c.value_cap, consumed_value=c.consumed_value,
        plants=";".join(c.plants), source=c.source, added_by=added_by,
    )
    row.lines = [ContractLineRow(item_code=l.item_code, description=l.description, uom=l.uom, rate=l.rate) for l in c.lines]
    return row


def seed_if_empty(db: Session) -> None:
    """Load the kit on first start. Idempotent."""
    if db.scalar(select(ItemRow).limit(1)) is not None:
        return
    log.info("Seeding database from kit at %s", KIT_DIR)
    md = kit_loader.load_master_data(KIT_DIR, include_pdf_contracts=True)
    db.add_all(ItemRow(**vars(i)) for i in md.items.values())
    db.add_all(
        SupplierRow(
            supplier_id=s.supplier_id, name=s.name, city=s.city, categories=";".join(s.categories),
            status=s.status, on_time_pct=s.on_time_pct, quality_rating=s.quality_rating,
        )
        for s in md.suppliers.values()
    )
    db.add_all(PoHistoryRow(**vars(p)) for p in md.purchase_orders)
    db.add_all(PlantRow(plant=k, name=v) for k, v in md.plants.items())
    for c in md.contracts:
        db.add(contract_to_row(c))
        if c.source.startswith("pdf:"):
            audit(db, "system", "contract.import_pdf", "contract", c.contract_id,
                  "PDF-only contract extracted from the signed copy at setup", source=c.source)
    policy = Policy.model_validate(json.loads(POLICY_SEED.read_text(encoding="utf-8")))
    db.add(
        PolicyVersionRow(
            version=policy.version, effective_from=policy.effective_from, status="active",
            document=policy.model_dump(mode="json", by_alias=True), source_kind="seed",
            source_name="Corvo_Procurement_Policy.pdf (v5.0)", created_by="system", decided_by="system",
            changes=[{"kind": "baseline", "text": "Baseline policy v5.0 loaded"}],
        )
    )
    audit(db, "system", "policy.activate", "policy", policy.version, "Baseline policy loaded at setup")
    db.commit()


def load_master_data(db: Session) -> MasterData:
    contracts = [
        Contract(
            r.contract_id, r.supplier_id, r.supplier_name, r.start_date, r.end_date, r.value_cap, r.consumed_value,
            tuple(ContractLine(l.item_code, l.description, l.uom, l.rate) for l in r.lines),
            tuple(p for p in r.plants.split(";") if p), r.source,
        )
        for r in db.scalars(select(ContractRow))
    ]
    return MasterData(
        items={r.item_code: Item(r.item_code, r.description, r.uom, r.category, r.item_type, r.standard_price)
               for r in db.scalars(select(ItemRow))},
        suppliers={
            r.supplier_id: Supplier(r.supplier_id, r.name, r.city, tuple(c for c in r.categories.split(";") if c),
                                    r.status, r.on_time_pct, r.quality_rating)
            for r in db.scalars(select(SupplierRow))
        },
        purchase_orders=[
            PurchaseOrder(r.po_number, r.po_date, r.supplier_id, r.item_code, r.quantity, r.uom, r.unit_price,
                          r.plant, r.delivered_on_time)
            for r in db.scalars(select(PoHistoryRow))
        ],
        contracts=contracts,
        plants={r.plant: r.name for r in db.scalars(select(PlantRow))},
    )
