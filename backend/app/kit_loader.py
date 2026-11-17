"""Load the task kit's CSV / PDF files into engine objects.

Used to seed the database and directly by the tests.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .engine.types import Contract, ContractLine, Item, MasterData, PurchaseOrder, Supplier
from .services.contract_pdf import extract_contract


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_items(kit: Path) -> list[Item]:
    return [
        Item(r["item_code"], r["description"], r["uom"], r["category"], r["item_type"], float(r["standard_price"]))
        for r in _rows(kit / "master_data" / "item_master.csv")
    ]


def load_suppliers(kit: Path) -> list[Supplier]:
    return [
        Supplier(
            r["supplier_id"], r["name"], r["city"],
            tuple(c.strip() for c in r["categories"].split(";") if c.strip()),
            r["status"], float(r["on_time_pct"]), float(r["quality_rating"]),
        )
        for r in _rows(kit / "master_data" / "supplier_master.csv")
    ]


def load_pos(kit: Path) -> list[PurchaseOrder]:
    return [
        PurchaseOrder(
            r["po_number"], date.fromisoformat(r["po_date"]), r["supplier_id"], r["item_code"],
            float(r["quantity"]), r["uom"], float(r["unit_price"]), r["plant"],
            r["delivered_on_time"].strip().lower() == "true",
        )
        for r in _rows(kit / "master_data" / "po_history.csv")
    ]


def load_plants(kit: Path) -> dict[str, str]:
    return {r["plant"]: r["name"] for r in _rows(kit / "master_data" / "plants.csv")}


def load_register_contracts(kit: Path) -> list[Contract]:
    lines: dict[str, list[ContractLine]] = {}
    for r in _rows(kit / "contracts" / "contract_lines.csv"):
        lines.setdefault(r["contract_id"], []).append(ContractLine(r["item_code"], r["description"], r["uom"], float(r["rate"])))
    return [
        Contract(
            r["contract_id"], r["supplier_id"], r["supplier_name"],
            date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"]),
            float(r["value_cap"]), float(r["consumed_value"]), tuple(lines.get(r["contract_id"], [])),
        )
        for r in _rows(kit / "contracts" / "contract_register.csv")
    ]


def load_pdf_only_contracts(kit: Path, known_ids: set[str]) -> list[Contract]:
    """Contracts that exist only as signed PDFs (4.4). They start with zero consumption."""
    out = []
    for pdf in sorted((kit / "contracts" / "pdf").glob("*.pdf")):
        ex = extract_contract(pdf.read_bytes())
        if not ex.complete or ex.contract_id in known_ids:
            continue
        out.append(
            Contract(
                ex.contract_id, ex.supplier_id, ex.supplier_name,
                date.fromisoformat(ex.start_date), date.fromisoformat(ex.end_date),
                ex.value_cap, 0.0,
                tuple(ContractLine(l.item_code, l.description, l.uom, l.rate) for l in ex.lines),
                source=f"pdf:{pdf.name}",
            )
        )
    return out


def load_master_data(kit: Path, include_pdf_contracts: bool = True) -> MasterData:
    contracts = load_register_contracts(kit)
    if include_pdf_contracts:
        contracts += load_pdf_only_contracts(kit, {c.contract_id for c in contracts})
    return MasterData(
        items={i.item_code: i for i in load_items(kit)},
        suppliers={s.supplier_id: s for s in load_suppliers(kit)},
        purchase_orders=load_pos(kit),
        contracts=contracts,
        plants=load_plants(kit),
    )
