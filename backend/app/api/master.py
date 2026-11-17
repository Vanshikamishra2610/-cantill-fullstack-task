"""Master data, contracts, preferred suppliers and chat intake."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..engine.matcher import ItemMatcher
from ..models import ContractRow, ItemRow, PlantRow, SupplierRow
from ..services import contracts as contract_svc
from ..services import suppliers as supplier_svc
from ..services.chat import ChatService
from ..services.llm import llm_available, provider_label
from ..services.master import load_master_data
from .deps import actor

router = APIRouter(prefix="/api", tags=["master data"])


# ----------------------------- master data ----------------------------------
@router.get("/items")
def items(q: str | None = None, db: Session = Depends(get_session)):
    rows = list(db.scalars(select(ItemRow).order_by(ItemRow.item_code)))
    out = [{"item_code": r.item_code, "description": r.description, "uom": r.uom, "category": r.category,
            "item_type": r.item_type, "standard_price": r.standard_price} for r in rows]
    if q:
        md = load_master_data(db)
        ranked = {c.item_code: c.score for c in ItemMatcher(md.items).rank(q, limit=10)}
        out = sorted((o for o in out if o["item_code"] in ranked), key=lambda o: -ranked[o["item_code"]])
        for o in out:
            o["score"] = ranked[o["item_code"]]
    return out


@router.get("/suppliers")
def suppliers(db: Session = Depends(get_session)):
    return [{"supplier_id": r.supplier_id, "name": r.name, "city": r.city, "categories": r.categories.split(";"),
             "status": r.status, "on_time_pct": r.on_time_pct, "quality_rating": r.quality_rating}
            for r in db.scalars(select(SupplierRow).order_by(SupplierRow.supplier_id))]


@router.get("/plants")
def plants(db: Session = Depends(get_session)):
    return [{"plant": r.plant, "name": r.name} for r in db.scalars(select(PlantRow))]


# ----------------------------- contracts ------------------------------------
@router.get("/contracts")
def contracts(db: Session = Depends(get_session)):
    return [contract_svc.serialize(r) for r in db.scalars(select(ContractRow).order_by(ContractRow.start_date))]


@router.post("/contracts/extract")
async def extract_contract(file: UploadFile = File(...), db: Session = Depends(get_session)):
    """Read a signed contract PDF into fields for the buyer to review (nothing is saved)."""
    data = await file.read()
    try:
        return contract_svc.extract_from_pdf(db, data, file.filename or "contract.pdf")
    except Exception as exc:  # malformed PDF
        raise HTTPException(400, f"could not read the PDF: {exc}") from exc


@router.post("/contracts")
def add_contract(body: dict, db: Session = Depends(get_session), who: str = Depends(actor)):
    try:
        data = contract_svc.ContractIn.model_validate(body)
        return contract_svc.serialize(contract_svc.add_contract(db, data, who))
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(e["msg"] for e in exc.errors())) from exc
    except contract_svc.ContractError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/contracts/register")
async def load_register(register_file: UploadFile = File(..., alias="register"), lines: UploadFile = File(...),
                        db: Session = Depends(get_session), who: str = Depends(actor)):
    """Load / refresh the contract register from contract_register.csv + contract_lines.csv."""
    try:
        return contract_svc.load_register_csv(
            db, (await register_file.read()).decode("utf-8-sig"), (await lines.read()).decode("utf-8-sig"), who)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, f"register files are not in the kit format: {exc}") from exc


# ----------------------------- preferred suppliers --------------------------
@router.get("/preferred-suppliers")
def preferred(as_of: date = Query(default_factory=date.today), item_code: str | None = None,
              db: Session = Depends(get_session)):
    return supplier_svc.preferred_lists(db, as_of, item_code)


@router.get("/preferred-suppliers/export.csv")
def preferred_export(as_of: date = Query(default_factory=date.today), top: int = 3, db: Session = Depends(get_session)):
    return PlainTextResponse(
        supplier_svc.export_csv(db, as_of, top), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="preferred_suppliers_{as_of.isoformat()}.csv"'},
    )


# ----------------------------- chat intake ----------------------------------
class ChatIn(BaseModel):
    session_id: str | None = None
    message: str = ""
    option: str | None = None
    department: str = ""


@router.post("/chat")
def chat(body: ChatIn, db: Session = Depends(get_session), who: str = Depends(actor)):
    return ChatService(db).handle(body.session_id, body.message, body.option, who, body.department)


@router.get("/health")
def health():
    return {"status": "ok", "ai": llm_available(), "ai_provider": provider_label()}
