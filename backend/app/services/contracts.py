"""Contract register: list, load from CSV, add a PDF-only contract."""
from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ContractLineRow, ContractRow, ItemRow, SupplierRow
from .contract_pdf import ExtractedContract, ExtractedLine, extract_contract, pdf_text
from .llm import LLMUnavailable, extract_json, llm_available
from .master import audit


class ContractError(ValueError):
    pass


class ContractLineIn(BaseModel):
    item_code: str
    description: str = ""
    uom: str = ""
    rate: float = Field(gt=0)


class ContractIn(BaseModel):
    contract_id: str
    supplier_id: str
    supplier_name: str = ""
    start_date: date
    end_date: date
    value_cap: float = Field(gt=0)
    consumed_value: float = Field(default=0, ge=0)
    plants: list[str] = []
    lines: list[ContractLineIn]
    source: str = "manual"

    @field_validator("contract_id", "supplier_id")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _check(self) -> "ContractIn":
        if self.end_date < self.start_date:
            raise ValueError("end date is before start date")
        if not self.lines:
            raise ValueError("a contract needs at least one item line")
        return self


def serialize(row: ContractRow, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    status = "active" if row.start_date <= today <= row.end_date else ("expired" if row.end_date < today else "future")
    return {
        "contract_id": row.contract_id, "supplier_id": row.supplier_id, "supplier_name": row.supplier_name,
        "start_date": row.start_date.isoformat(), "end_date": row.end_date.isoformat(),
        "value_cap": row.value_cap, "consumed_value": row.consumed_value,
        "remaining": round(row.value_cap - row.consumed_value, 2), "plants": [p for p in row.plants.split(";") if p],
        "source": row.source, "added_by": row.added_by, "added_at": row.added_at.isoformat(timespec="seconds"),
        "validity": status,
        "lines": [{"item_code": l.item_code, "description": l.description, "uom": l.uom, "rate": l.rate} for l in row.lines],
    }


def add_contract(db: Session, data: ContractIn, actor: str) -> ContractRow:
    if db.get(ContractRow, data.contract_id):
        raise ContractError(f"contract {data.contract_id} is already in the register")
    supplier = db.get(SupplierRow, data.supplier_id)
    if supplier is None:
        raise ContractError(f"supplier {data.supplier_id} is not in the supplier master")
    items = {r.item_code: r for r in db.scalars(select(ItemRow))}
    unknown = [l.item_code for l in data.lines if l.item_code not in items]
    if unknown:
        raise ContractError(f"items not in the item master: {', '.join(unknown)}")
    row = ContractRow(
        contract_id=data.contract_id, supplier_id=data.supplier_id, supplier_name=data.supplier_name or supplier.name,
        start_date=data.start_date, end_date=data.end_date, value_cap=data.value_cap,
        consumed_value=data.consumed_value, plants=";".join(data.plants), source=data.source, added_by=actor,
    )
    row.lines = [
        ContractLineRow(item_code=l.item_code, description=l.description or items[l.item_code].description,
                        uom=l.uom or items[l.item_code].uom, rate=l.rate)
        for l in data.lines
    ]
    db.add(row)
    audit(db, actor, "contract.add", "contract", data.contract_id, f"Added from {data.source}",
          supplier_id=data.supplier_id, warning=None if supplier.status == "Active" else f"supplier is {supplier.status}")
    db.commit()
    return row


LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "contract_id": {"type": "string"}, "supplier_id": {"type": "string"}, "supplier_name": {"type": "string"},
        "start_date": {"type": "string"}, "end_date": {"type": "string"}, "value_cap": {"type": "number"},
        "plants": {"type": "array", "items": {"type": "string"}},
        "lines": {"type": "array", "items": {
            "type": "object",
            "properties": {"item_code": {"type": "string"}, "description": {"type": "string"},
                           "uom": {"type": "string"}, "rate": {"type": "number"}},
            "required": ["item_code", "description", "uom", "rate"], "additionalProperties": False,
        }},
    },
    "required": ["contract_id", "supplier_id", "supplier_name", "start_date", "end_date", "value_cap", "plants", "lines"],
    "additionalProperties": False,
}


def extract_from_pdf(db: Session, data: bytes, filename: str) -> dict[str, Any]:
    """Template parser first; LLM if the template does not fit. Buyer reviews before saving."""
    extracted = extract_contract(data)
    if not extracted.complete and llm_available():
        try:
            raw = extract_json(
                "You extract rate contract fields from signed contract PDFs. Dates as YYYY-MM-DD, amounts as numbers in INR.",
                f"Extract the contract from this PDF ({filename}). Text layer:\n{pdf_text(data)}",
                LLM_SCHEMA, pdf_bytes=data, effort="low",
            )
            extracted = ExtractedContract(
                **{**raw, "lines": [ExtractedLine(**l) for l in raw["lines"]]}, method="llm"
            )
        except (LLMUnavailable, TypeError, ValueError) as exc:
            extracted.warnings.append(f"AI extraction unavailable: {exc}")
    result = extracted.to_dict()
    result["already_registered"] = bool(extracted.contract_id and db.get(ContractRow, extracted.contract_id))
    supplier = db.get(SupplierRow, extracted.supplier_id) if extracted.supplier_id else None
    if supplier and supplier.status != "Active":
        result["warnings"].append(f"supplier {supplier.supplier_id} is {supplier.status}: the contract will not apply (4.1)")
    if extracted.plants == ["CHK1", "PUN1"]:
        result["plants"] = []  # both plants = no restriction
    return result


def load_register_csv(db: Session, register_csv: str, lines_csv: str, actor: str) -> dict[str, int]:
    """Replace/insert register rows from the kit's two CSV files."""
    lines: dict[str, list[dict[str, str]]] = {}
    for r in csv.DictReader(io.StringIO(lines_csv.lstrip("﻿"))):
        lines.setdefault(r["contract_id"].strip(), []).append(r)
    added = updated = 0
    for r in csv.DictReader(io.StringIO(register_csv.lstrip("﻿"))):
        cid = r["contract_id"].strip()
        existing = db.get(ContractRow, cid)
        if existing:
            db.delete(existing)
            db.flush()
            updated += 1
        else:
            added += 1
        row = ContractRow(
            contract_id=cid, supplier_id=r["supplier_id"].strip(), supplier_name=r["supplier_name"].strip(),
            start_date=date.fromisoformat(r["start_date"].strip()), end_date=date.fromisoformat(r["end_date"].strip()),
            value_cap=float(r["value_cap"]), consumed_value=float(r.get("consumed_value") or 0),
            source="register", added_by=actor,
        )
        row.lines = [ContractLineRow(item_code=l["item_code"], description=l["description"], uom=l["uom"],
                                     rate=float(l["rate"])) for l in lines.get(cid, [])]
        db.add(row)
    audit(db, actor, "contract.load_register", "contract", "register", f"{added} added, {updated} updated")
    db.commit()
    return {"added": added, "updated": updated}
