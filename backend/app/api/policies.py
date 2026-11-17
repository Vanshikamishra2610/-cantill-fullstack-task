"""Policy versions: upload / describe / edit a change, review it, approve it."""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..engine.amend import AmendmentError
from ..engine.policy import FACTS, parameter_catalogue
from ..models import PolicyVersionRow
from ..services import policies as svc
from ..services.llm import LLMUnavailable, llm_available
from ..services.policy_reader import document_text
from .deps import actor

router = APIRouter(prefix="/api/policies", tags=["policy"])


def _row(db: Session, policy_id: int) -> PolicyVersionRow:
    row = db.get(PolicyVersionRow, policy_id)
    if row is None:
        raise HTTPException(404, "policy version not found")
    return row


def _draft_errors(fn):
    try:
        return svc.serialize(fn(), with_document=True)
    except LLMUnavailable as exc:
        raise HTTPException(503, f"The AI policy reader is unavailable ({exc}). "
                                 "Use the structured editor to make the change instead.") from exc
    except (AmendmentError, svc.PolicyError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("")
def list_versions(db: Session = Depends(get_session)):
    rows = db.scalars(select(PolicyVersionRow).order_by(PolicyVersionRow.id.desc()))
    return {"versions": [svc.serialize(r) for r in rows], "ai_reader": llm_available(),
            "in_force_today": svc.policy_book(db).for_date(date.today()).version}


@router.get("/catalogue")
def catalogue():
    """What a rule can test and which parameters exist - used by the editor."""
    return {
        "facts": FACTS,
        "ops": ["eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "contains", "exists"],
        "parameters": parameter_catalogue(),
        "decisions": ["PO", "SOURCING_REQUEST", "NOT_PROCESSED"],
        "supplier_modes": ["contract", "preferred", "suggest", "none"],
    }


@router.get("/{policy_id}")
def get_version(policy_id: int, db: Session = Depends(get_session)):
    return svc.serialize(_row(db, policy_id), with_document=True)


@router.post("/drafts/upload")
async def draft_from_upload(file: UploadFile = File(...), note: str = Form(""), db: Session = Depends(get_session),
                            who: str = Depends(actor)):
    """Upload an amendment / new policy document (.pdf, .docx, .txt, .md). The AI reader proposes the changes."""
    data = await file.read()
    try:
        text = document_text(file.filename or "upload.txt", data)
    except Exception as exc:  # unreadable file of any kind
        raise HTTPException(400, f"could not read {file.filename}: {exc}") from exc
    if note.strip():
        text = f"{text}\n\nNote from the uploader: {note.strip()}"
    return _draft_errors(lambda: svc.draft_from_text(db, text, who, "document", file.filename or ""))


class ChatChangeIn(BaseModel):
    text: str


@router.post("/drafts/chat")
def draft_from_chat(body: ChatChangeIn, db: Session = Depends(get_session), who: str = Depends(actor)):
    """Describe a change in plain words, e.g. 'From 1 Dec the spot PO limit at CHK1 is 3 lakh'."""
    return _draft_errors(lambda: svc.draft_from_text(db, body.text, who, "chat", ""))


class OperationsIn(BaseModel):
    operations: list[dict[str, Any]]
    version: str | None = None
    effective_from: date
    summary: str = ""


@router.post("/drafts/operations")
def draft_from_operations(body: OperationsIn, db: Session = Depends(get_session), who: str = Depends(actor)):
    return _draft_errors(lambda: svc.draft_from_operations(db, body.operations, body.version, body.effective_from,
                                                           body.summary, who))


class DocumentIn(BaseModel):
    document: dict[str, Any]


@router.post("/drafts/document")
def draft_from_document(body: DocumentIn, db: Session = Depends(get_session), who: str = Depends(actor)):
    return _draft_errors(lambda: svc.draft_from_document(db, body.document, who))


class DecisionIn(BaseModel):
    effective_from: date | None = None
    comment: str = ""


@router.post("/{policy_id}/approve")
def approve(policy_id: int, body: DecisionIn, db: Session = Depends(get_session), who: str = Depends(actor)):
    try:
        return svc.serialize(svc.approve(db, _row(db, policy_id), who, body.effective_from, body.comment), True)
    except (svc.PolicyError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{policy_id}/reject")
def reject(policy_id: int, body: DecisionIn, db: Session = Depends(get_session), who: str = Depends(actor)):
    try:
        return svc.serialize(svc.reject(db, _row(db, policy_id), who, body.comment))
    except svc.PolicyError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{policy_id}/withdraw")
def withdraw(policy_id: int, body: DecisionIn, db: Session = Depends(get_session), who: str = Depends(actor)):
    try:
        return svc.serialize(svc.withdraw(db, _row(db, policy_id), who, body.comment))
    except svc.PolicyError as exc:
        raise HTTPException(409, str(exc)) from exc
