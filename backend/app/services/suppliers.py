"""Preferred supplier lists for buyers and the CSV export (policy section 6)."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from ..engine.csv_io import rows_to_csv
from ..engine.suppliers import build_preferred_list
from .master import load_master_data
from .policies import policy_book

EXPORT_COLUMNS = ["item_code", "rank", "supplier_id", "reference_price"]


def preferred_lists(db: Session, as_of: date, item_code: str | None = None) -> dict[str, Any]:
    md = load_master_data(db)
    policy = policy_book(db).for_date(as_of)
    items = [md.items[item_code]] if item_code else sorted(md.items.values(), key=lambda i: i.item_code)
    out = []
    for item in items:
        params = policy.params_for({"plant": "", "item_code": item.item_code, "category": item.category,
                                    "item_type": item.item_type, "urgency": "", "is_emergency": False,
                                    "department": ""})
        entries = build_preferred_list(md, item.item_code, as_of, params)
        out.append({
            "item_code": item.item_code, "description": item.description, "category": item.category,
            "entries": [e.to_dict() for e in entries],
        })
    return {"as_of": as_of.isoformat(), "policy_version": policy.version,
            "weights": policy.parameters.preferred_list.weights.model_dump(), "items": out}


def export_csv(db: Session, as_of: date, top: int = 3) -> str:
    data = preferred_lists(db, as_of)
    rows = [
        {"item_code": it["item_code"], "rank": e["rank"], "supplier_id": e["supplier_id"],
         "reference_price": "" if e["reference_price"] is None else round(e["reference_price"], 2)}
        for it in data["items"] for e in it["entries"][:top]
    ]
    return rows_to_csv(rows, EXPORT_COLUMNS)
