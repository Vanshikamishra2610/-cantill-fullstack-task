"""Processing runs and the buyer workspace actions (approve / override)."""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.csv_io import EXPORT_COLUMNS, PR_COLUMNS, rows_to_csv
from ..engine.pipeline import BatchResult, DecisionEngine, LineResult, Requirement
from ..engine.types import MasterData, PRLine
from ..models import DocumentRow, PurchaseRequestRow, RunLineRow, RunRequirementRow, RunRow
from .master import audit, load_master_data
from .policies import policy_book

ROUTES = ["CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO", "RFQ", "RFP", "CAPEX_SOURCING", "NEW_ITEM_SOURCING"]
PO_ROUTES = {"CONTRACT_PO", "EMERGENCY_PO", "SPOT_PO"}


class WorkspaceError(ValueError):
    pass


# ----------------------------- serialisation ---------------------------------
def line_to_input_row(line: PRLine) -> dict[str, Any]:
    return {
        "pr_no": line.pr_no,
        "pr_line": line.pr_line,
        "created_at": line.created_at.strftime("%Y-%m-%d %H:%M") if line.created_at else "",
        "requester": line.requester,
        "department": line.department,
        "plant": line.plant,
        "item_code": line.item_code,
        "description": line.description,
        "quantity": "" if line.quantity is None else f"{line.quantity:g}",
        "uom": line.uom,
        "estimated_unit_price": "" if line.estimated_unit_price is None else line.estimated_unit_price,
        "need_by_date": line.need_by_date.isoformat() if line.need_by_date else "",
        "urgency": line.urgency,
    }


def _line_detail(lr: LineResult) -> dict[str, Any]:
    m = lr.match
    return {
        **line_to_input_row(lr.line),
        "resolved_item_code": lr.resolved_item_code,
        "match": None if m is None else {
            "status": m.status, "method": m.method, "score": round(m.score, 3),
            "candidates": [vars(c) for c in m.candidates[:3]],
        },
        "errors": lr.errors,
        "notes": lr.notes,
        "requirement_id": lr.requirement_id,
    }


def requirement_detail(req: Requirement, md: MasterData) -> dict[str, Any]:
    def supplier_card(sid: str) -> dict[str, Any]:
        entry = next((e for e in req.preferred if e.supplier_id == sid), None)
        sup = md.suppliers[sid]
        return {
            "supplier_id": sid,
            "name": sup.name,
            "on_time_pct": sup.on_time_pct,
            "quality_rating": sup.quality_rating,
            "source": f"preferred list rank {entry.rank}" if entry else "category top-up (6.4)",
            "reference_price": entry.reference_price if entry else None,
        }

    return {
        "group_id": req.id,
        "item_code": req.item_code,
        "item_description": req.item_description,
        "category": req.category,
        "item_type": req.item_type,
        "uom": req.uom,
        "plant": req.plant,
        "requirement_date": req.requirement_date.isoformat(),
        "created_at": req.created_at.isoformat(timespec="minutes"),
        "is_emergency": req.is_emergency,
        "policy_version": req.policy.version,
        "rule": {"id": req.rule.id, "clause": req.rule.clause, "title": req.rule.title} if req.rule else None,
        "decision": req.decision,
        "route": req.route,
        "supplier_id": req.supplier_id,
        "supplier_name": req.supplier_name,
        "unit_price": req.unit_price,
        "contract_id": req.contract_id,
        "reference": req.reference,
        "quantity": req.quantity,
        "valuation_price": req.valuation_price,
        "price_source": req.price_source,
        "value": req.value,
        "po_id": req.po_id,
        "suggestions": [supplier_card(s) for s in req.suggestions],
        "preferred": [e.to_dict() for e in req.preferred[:10]],
        "contract_review": req.contract_review,
        "facts": req.facts,
        "reason": req.reason,
        "notes": req.notes,
        "lines": [f"{lr.line.pr_no}/{lr.line.pr_line}" for lr in req.lines],
    }


def _summary(result: BatchResult) -> dict[str, Any]:
    rows = result.export_rows()
    return {
        "lines": len(rows),
        "requirements": len(result.requirements),
        "purchase_orders": len(result.purchase_orders),
        "routes": dict(Counter(r["route"] for r in rows)),
        "not_processed": sum(1 for r in rows if r["route"] == "NOT_PROCESSED"),
        "total_value": round(sum(r.value for r in result.requirements), 2),
    }


# ----------------------------- runs -----------------------------------------
def run_engine(db: Session, lines: list[PRLine]) -> tuple[BatchResult, MasterData]:
    md = load_master_data(db)
    engine = DecisionEngine(md, policy_book(db))
    return engine.process(lines), md


def create_run(db: Session, lines: list[PRLine], name: str, source: str, actor: str) -> RunRow:
    result, md = run_engine(db, lines)
    run = RunRow(
        name=name, source=source, created_by=actor,
        input_csv=rows_to_csv([line_to_input_row(l) for l in lines], PR_COLUMNS),
        policy_versions=result.versions_used, summary=_summary(result),
    )
    run.requirements = [RunRequirementRow(group_id=r.id, detail=requirement_detail(r, md)) for r in result.requirements]
    run.lines = [
        RunLineRow(seq=i, row=row, detail=_line_detail(lr))
        for i, (row, lr) in enumerate(zip(result.export_rows(), result.lines))
    ]
    db.add(run)
    db.flush()
    audit(db, actor, "run.process", "run", str(run.id), f"Processed {len(lines)} PR lines ({name})",
          policy_versions=result.versions_used, summary=run.summary)
    db.commit()
    return run


def process_open_chat_prs(db: Session, actor: str) -> RunRow:
    rows = list(db.scalars(select(PurchaseRequestRow).where(PurchaseRequestRow.status == "open")))
    if not rows:
        raise WorkspaceError("there are no open PRs from chat intake")
    lines = [pr_row_to_line(r) for r in rows]
    run = create_run(db, lines, f"Chat PRs ({len(lines)} lines)", "chat", actor)
    for r in rows:
        r.status = "processed"
    db.commit()
    return run


def pr_row_to_line(r: PurchaseRequestRow) -> PRLine:
    return PRLine(r.pr_no, r.pr_line, r.created_at, r.requester, r.department, r.plant, r.item_code,
                  r.description, r.quantity, r.uom, r.estimated_unit_price, r.need_by_date, r.urgency)


def export_csv(run: RunRow, include_overrides: bool = False) -> str:
    rows = [dict(l.row) for l in run.lines]
    if include_overrides:
        finals = {r.group_id: r for r in run.requirements if r.final}
        for row in rows:
            req = finals.get(row["group_id"])
            if req:
                f = req.final
                row.update(
                    po_id=f.get("po_id") or "", decision=f["decision"], route=f["route"],
                    supplier_id=f.get("supplier_id") or "", unit_price=f.get("unit_price") or "",
                    suggested_supplier_ids=";".join(f.get("suggested_supplier_ids", [])),
                    reason=f"OVERRIDDEN by {f['by']}: {f['comment']} | engine: {row['reason']}",
                )
    return rows_to_csv(rows, EXPORT_COLUMNS)


def serialize_run(run: RunRow, full: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": run.id, "name": run.name, "source": run.source, "created_by": run.created_by,
        "created_at": run.created_at.isoformat(timespec="seconds"), "policy_versions": run.policy_versions,
        "summary": run.summary,
        "status_counts": dict(Counter(r.status for r in run.requirements)),
    }
    if full:
        out["requirements"] = [serialize_requirement(r) for r in run.requirements]
        out["lines"] = [{"row": l.row, "detail": l.detail} for l in run.lines]
    return out


def serialize_requirement(r: RunRequirementRow) -> dict[str, Any]:
    return {"group_id": r.group_id, "status": r.status, "document_id": r.document_id, "final": r.final, **r.detail}


# ----------------------------- workspace actions -----------------------------
def _get_req(run: RunRow, group_id: str) -> RunRequirementRow:
    req = next((r for r in run.requirements if r.group_id == group_id), None)
    if req is None:
        raise WorkspaceError(f"requirement {group_id} not found in run {run.id}")
    return req


def _doc_kind(route: str) -> str:
    return {"CAPEX_SOURCING": "CAPEX", "NEW_ITEM_SOURCING": "NEWITEM"}.get(route, "PO" if route in PO_ROUTES else route)


def approve(db: Session, run: RunRow, group_id: str, actor: str, comment: str = "") -> DocumentRow:
    """Approve the engine decision. For a PO, every requirement consolidated on that PO is approved together."""
    req = _get_req(run, group_id)
    if req.status != "pending":
        raise WorkspaceError(f"{group_id} is already {req.status}")
    d = req.detail
    if d["decision"] == "PO":
        members = [r for r in run.requirements if r.detail.get("po_id") == d["po_id"] and r.status == "pending"]
        doc_id = f"R{run.id}-{d['po_id']}"
        payload_lines = [
            {"group_id": m.group_id, "item_code": m.detail["item_code"], "description": m.detail["item_description"],
             "quantity": m.detail["quantity"], "uom": m.detail["uom"], "unit_price": m.detail["unit_price"],
             "value": round(m.detail["quantity"] * (m.detail["unit_price"] or 0), 2), "pr_lines": m.detail["lines"]}
            for m in members
        ]
        doc = DocumentRow(
            id=doc_id, kind="PO", run_id=run.id, group_ids=[m.group_id for m in members],
            supplier_id=d["supplier_id"], plant=d["plant"], value=round(sum(l["value"] for l in payload_lines), 2),
            payload={"route": d["route"], "policy_version": d["policy_version"], "lines": payload_lines,
                     "reference": d["reference"]},
            created_by=actor,
        )
    else:
        members = [req]
        kind = _doc_kind(d["route"])
        seq = 1 + sum(1 for r in run.requirements if r.document_id and r.document_id.startswith(f"R{run.id}-{kind}-"))
        doc = DocumentRow(
            id=f"R{run.id}-{kind}-{seq:04d}", kind=kind, run_id=run.id, group_ids=[group_id], supplier_id=None,
            suppliers_invited=[s["supplier_id"] for s in d["suggestions"]], plant=d["plant"], value=d["value"],
            payload={"route": d["route"], "policy_version": d["policy_version"], "item_code": d["item_code"],
                     "description": d["item_description"], "quantity": d["quantity"], "uom": d["uom"],
                     "pr_lines": d["lines"], "needs_capex_approval": d["route"] == "CAPEX_SOURCING"},
            created_by=actor,
        )
    db.add(doc)
    for m in members:
        m.status, m.document_id = "approved", doc.id
    audit(db, actor, "decision.approve", "requirement", f"run{run.id}/{group_id}", comment,
          document_id=doc.id, route=d["route"], groups=[m.group_id for m in members])
    db.commit()
    return doc


def override(db: Session, run: RunRow, group_id: str, actor: str, route: str, comment: str,
             supplier_id: str | None, unit_price: float | None, suggested: list[str],
             md: MasterData) -> DocumentRow:
    """Replace the engine decision with the buyer's, keep the original, and create the document."""
    if not comment or len(comment.strip()) < 5:
        raise WorkspaceError("an override needs a written reason (at least a few words)")
    route = route.strip().upper()
    if route not in ROUTES:
        raise WorkspaceError(f"route must be one of {', '.join(ROUTES)}")
    req = _get_req(run, group_id)
    if req.status != "pending":
        raise WorkspaceError(f"{group_id} is already {req.status}")
    d = req.detail
    is_po = route in PO_ROUTES
    allowed = {s for s, sup in md.suppliers.items() if sup.status == "Active"}
    if is_po:
        if not supplier_id or supplier_id not in md.suppliers:
            raise WorkspaceError("a PO override needs a valid supplier")
        if supplier_id not in allowed:
            raise WorkspaceError(f"{supplier_id} is {md.suppliers[supplier_id].status}; policy 5.7 forbids POs to it")
        if unit_price is None or unit_price <= 0:
            raise WorkspaceError("a PO override needs a unit price")
    bad = [s for s in suggested if s not in allowed]
    if bad:
        raise WorkspaceError(f"cannot suggest {', '.join(bad)}: not Active (policy 5.7)")

    kind = _doc_kind(route)
    seq = 1 + sum(1 for r in run.requirements if r.document_id and r.document_id.startswith(f"R{run.id}-OVR-"))
    doc_id = f"R{run.id}-OVR-{kind}-{seq:04d}"
    value = round(d["quantity"] * unit_price, 2) if is_po else d["value"]
    req.final = {
        "decision": "PO" if is_po else "SOURCING_REQUEST", "route": route,
        "supplier_id": supplier_id if is_po else None, "unit_price": unit_price if is_po else None,
        "suggested_supplier_ids": [] if is_po else suggested, "po_id": doc_id if is_po else None,
        "value": value, "comment": comment.strip(), "by": actor, "original_route": d["route"],
    }
    req.status, req.document_id = "overridden", doc_id
    db.add(DocumentRow(
        id=doc_id, kind=kind, run_id=run.id, group_ids=[group_id], supplier_id=supplier_id if is_po else None,
        suppliers_invited=[] if is_po else suggested, plant=d["plant"], value=value,
        payload={"route": route, "override_of": d["route"], "comment": comment.strip(), "item_code": d["item_code"],
                 "quantity": d["quantity"], "unit_price": unit_price, "pr_lines": d["lines"]},
        created_by=actor,
    ))
    audit(db, actor, "decision.override", "requirement", f"run{run.id}/{group_id}", comment.strip(),
          original={"route": d["route"], "supplier_id": d["supplier_id"], "unit_price": d["unit_price"],
                    "suggestions": [s["supplier_id"] for s in d["suggestions"]]},
          new=req.final, document_id=doc_id)
    db.commit()
    return db.get(DocumentRow, doc_id)


def serialize_document(doc: DocumentRow) -> dict[str, Any]:
    return {
        "id": doc.id, "kind": doc.kind, "run_id": doc.run_id, "group_ids": doc.group_ids,
        "supplier_id": doc.supplier_id, "suppliers_invited": doc.suppliers_invited, "plant": doc.plant,
        "value": doc.value, "payload": doc.payload, "created_by": doc.created_by,
        "created_at": doc.created_at.isoformat(timespec="seconds"),
    }
