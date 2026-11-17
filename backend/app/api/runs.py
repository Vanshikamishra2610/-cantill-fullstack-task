"""Batch processing, runs and the buyer workspace."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..engine.csv_io import BatchFormatError, parse_pr_csv
from ..models import AuditRow, DocumentRow, PurchaseRequestRow, RunRow
from ..services import runs as svc
from ..services.master import load_master_data
from .deps import actor

router = APIRouter(prefix="/api", tags=["decisions"])


def _csv_response(text: str, filename: str) -> PlainTextResponse:
    return PlainTextResponse(text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


async def _read_batch(request: Request, file: UploadFile | None) -> tuple[str, str]:
    if file is not None:
        return (await file.read()).decode("utf-8-sig", errors="replace"), file.filename or "upload.csv"
    body = (await request.body()).decode("utf-8-sig", errors="replace")
    if not body.strip():
        raise HTTPException(400, "send the PR batch CSV as a multipart 'file' field or as the request body")
    return body, "api-request.csv"


@router.post("/process-batch")
async def process_batch(
    request: Request,
    file: UploadFile | None = File(default=None),
    format: str = Query("json", pattern="^(json|csv)$"),
    name: str | None = Query(None),
    db: Session = Depends(get_session),
    who: str = Depends(actor),
):
    """Take a PR batch CSV (sample format), decide every line, store the run, return the decisions."""
    text, filename = await _read_batch(request, file)
    try:
        lines = parse_pr_csv(text)
    except BatchFormatError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not lines:
        raise HTTPException(400, "the CSV has no PR lines")
    run = svc.create_run(db, lines, name or f"{filename} ({datetime.now():%d %b %H:%M})", "upload", who)
    if format == "csv":
        return _csv_response(svc.export_csv(run), f"decisions_run{run.id}.csv")
    return {"run_id": run.id, "policy_versions": run.policy_versions, "summary": run.summary,
            "decisions": [l.row for l in run.lines]}


@router.get("/runs")
def list_runs(db: Session = Depends(get_session)):
    return [svc.serialize_run(r) for r in db.scalars(select(RunRow).order_by(RunRow.id.desc()))]


def _run(db: Session, run_id: int) -> RunRow:
    run = db.get(RunRow, run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id} not found")
    return run


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_session)):
    return svc.serialize_run(_run(db, run_id), full=True)


@router.get("/runs/{run_id}/export.csv")
def export_run(run_id: int, include_overrides: bool = False, db: Session = Depends(get_session)):
    """Decisions in the brief's output format. By default the engine's decisions; optionally with buyer overrides."""
    run = _run(db, run_id)
    return _csv_response(svc.export_csv(run, include_overrides), f"decisions_run{run_id}.csv")


@router.post("/runs/{run_id}/reprocess")
def reprocess(run_id: int, db: Session = Depends(get_session), who: str = Depends(actor)):
    """Re-run the same input under the policy versions active now (e.g. after an amendment)."""
    run = _run(db, run_id)
    lines = parse_pr_csv(run.input_csv)
    new = svc.create_run(db, lines, f"{run.name} - reprocessed", run.source, who)
    return svc.serialize_run(new)


class ApproveIn(BaseModel):
    comment: str = ""


class OverrideIn(BaseModel):
    route: str
    comment: str = ""  # validated in the service, which gives a readable message
    supplier_id: str | None = None
    unit_price: float | None = None
    suggested_supplier_ids: list[str] = []


@router.post("/runs/{run_id}/requirements/{group_id}/approve")
def approve(run_id: int, group_id: str, body: ApproveIn, db: Session = Depends(get_session), who: str = Depends(actor)):
    try:
        doc = svc.approve(db, _run(db, run_id), group_id, who, body.comment)
    except svc.WorkspaceError as exc:
        raise HTTPException(409, str(exc)) from exc
    return svc.serialize_document(doc)


@router.post("/runs/{run_id}/requirements/{group_id}/override")
def override(run_id: int, group_id: str, body: OverrideIn, db: Session = Depends(get_session), who: str = Depends(actor)):
    try:
        doc = svc.override(db, _run(db, run_id), group_id, who, body.route, body.comment, body.supplier_id,
                           body.unit_price, body.suggested_supplier_ids, load_master_data(db))
    except svc.WorkspaceError as exc:
        raise HTTPException(422, str(exc)) from exc
    return svc.serialize_document(doc)


@router.get("/runs/{run_id}/requirements/{group_id}/history")
def requirement_history(run_id: int, group_id: str, db: Session = Depends(get_session)):
    rows = db.scalars(select(AuditRow).where(AuditRow.entity_id == f"run{run_id}/{group_id}").order_by(AuditRow.id))
    return [_audit(r) for r in rows]


@router.get("/documents")
def documents(db: Session = Depends(get_session)):
    return [svc.serialize_document(d) for d in db.scalars(select(DocumentRow).order_by(DocumentRow.created_at.desc()))]


@router.get("/prs")
def open_prs(db: Session = Depends(get_session)):
    rows = db.scalars(select(PurchaseRequestRow).order_by(PurchaseRequestRow.id.desc()))
    return [
        {"pr_no": r.pr_no, "pr_line": r.pr_line, "created_at": r.created_at.isoformat(timespec="minutes"),
         "requester": r.requester, "plant": r.plant, "item_code": r.item_code or "NEW", "description": r.description,
         "quantity": r.quantity, "uom": r.uom, "need_by_date": r.need_by_date.isoformat(), "urgency": r.urgency,
         "status": r.status}
        for r in rows
    ]


@router.post("/prs/process")
def process_prs(db: Session = Depends(get_session), who: str = Depends(actor)):
    try:
        run = svc.process_open_chat_prs(db, who)
    except svc.WorkspaceError as exc:
        raise HTTPException(409, str(exc)) from exc
    return svc.serialize_run(run)


def _audit(r: AuditRow) -> dict:
    return {"id": r.id, "at": r.at.isoformat(timespec="seconds"), "actor": r.actor, "action": r.action,
            "entity": r.entity, "entity_id": r.entity_id, "comment": r.comment, "details": r.details}


@router.get("/audit")
def audit_log(limit: int = 200, db: Session = Depends(get_session)):
    return [_audit(r) for r in db.scalars(select(AuditRow).order_by(AuditRow.id.desc()).limit(limit))]
