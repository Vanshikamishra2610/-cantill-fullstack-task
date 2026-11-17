"""Database tables.

Master data mirrors the kit files. Decisions are stored per *run* (one call of
the engine over a set of PR lines) so a batch can be re-processed under a new
policy version without losing the earlier result. Every buyer / procurement
action is written to ``audit_log``.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ----------------------------- master data -----------------------------------
class ItemRow(Base):
    __tablename__ = "items"
    item_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    description: Mapped[str] = mapped_column(String(200))
    uom: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(80))
    item_type: Mapped[str] = mapped_column(String(20))
    standard_price: Mapped[float] = mapped_column(Float)


class SupplierRow(Base):
    __tablename__ = "suppliers"
    supplier_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(80))
    categories: Mapped[str] = mapped_column(String(300))  # ';' separated, as in the kit
    status: Mapped[str] = mapped_column(String(20))
    on_time_pct: Mapped[float] = mapped_column(Float)
    quality_rating: Mapped[float] = mapped_column(Float)


class PoHistoryRow(Base):
    __tablename__ = "po_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    po_number: Mapped[str] = mapped_column(String(30), index=True)
    po_date: Mapped[date] = mapped_column(Date)
    supplier_id: Mapped[str] = mapped_column(String(20))
    item_code: Mapped[str] = mapped_column(String(20), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    uom: Mapped[str] = mapped_column(String(10))
    unit_price: Mapped[float] = mapped_column(Float)
    plant: Mapped[str] = mapped_column(String(10))
    delivered_on_time: Mapped[bool] = mapped_column(Boolean)


class PlantRow(Base):
    __tablename__ = "plants"
    plant: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class ContractRow(Base):
    __tablename__ = "contracts"
    contract_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(20))
    supplier_name: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    value_cap: Mapped[float] = mapped_column(Float)
    consumed_value: Mapped[float] = mapped_column(Float, default=0.0)
    plants: Mapped[str] = mapped_column(String(100), default="")  # ';' separated, blank = all
    source: Mapped[str] = mapped_column(String(100), default="register")
    added_by: Mapped[str] = mapped_column(String(100), default="system")
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lines: Mapped[list["ContractLineRow"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan", lazy="selectin"
    )


class ContractLineRow(Base):
    __tablename__ = "contract_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.contract_id"), index=True)
    item_code: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(200))
    uom: Mapped[str] = mapped_column(String(10))
    rate: Mapped[float] = mapped_column(Float)
    contract: Mapped[ContractRow] = relationship(back_populates="lines")


# ----------------------------- policy ----------------------------------------
class PolicyVersionRow(Base):
    """A policy version. Status: draft -> active (approved) | rejected."""

    __tablename__ = "policy_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(20))
    effective_from: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    document: Mapped[dict[str, Any]] = mapped_column(JSON)
    based_on_id: Mapped[int | None] = mapped_column(ForeignKey("policy_versions.id"), nullable=True)
    source_kind: Mapped[str] = mapped_column(String(20))  # seed | document | chat | editor
    source_name: Mapped[str] = mapped_column(String(200), default="")
    source_text: Mapped[str] = mapped_column(Text, default="")
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # plain-language diff
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_comment: Mapped[str] = mapped_column(Text, default="")


# ----------------------------- requests & runs -------------------------------
class PurchaseRequestRow(Base):
    """PR lines raised through chat intake (uploaded batches are stored on their run)."""

    __tablename__ = "purchase_requests"
    __table_args__ = (UniqueConstraint("pr_no", "pr_line"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pr_no: Mapped[str] = mapped_column(String(30))
    pr_line: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    requester: Mapped[str] = mapped_column(String(100))
    department: Mapped[str] = mapped_column(String(100), default="")
    plant: Mapped[str] = mapped_column(String(10))
    item_code: Mapped[str] = mapped_column(String(20), default="")
    description: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[float] = mapped_column(Float)
    uom: Mapped[str] = mapped_column(String(10), default="")
    estimated_unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    need_by_date: Mapped[date] = mapped_column(Date)
    urgency: Mapped[str] = mapped_column(String(20), default="Normal")
    source: Mapped[str] = mapped_column(String(20), default="chat")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | processed


class ChatSessionRow(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user: Mapped[str] = mapped_column(String(100))
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RunRow(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20))  # upload | api | chat
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    input_csv: Mapped[str] = mapped_column(Text)
    policy_versions: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requirements: Mapped[list["RunRequirementRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin", order_by="RunRequirementRow.id"
    )
    lines: Mapped[list["RunLineRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin", order_by="RunLineRow.seq"
    )


class RunRequirementRow(Base):
    """One requirement (group of clubbed lines) with the engine's decision and its current status."""

    __tablename__ = "run_requirements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    group_id: Mapped[str] = mapped_column(String(20))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)  # engine output, never edited
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | overridden
    final: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # decision after override
    document_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    run: Mapped[RunRow] = relationship(back_populates="requirements")


class RunLineRow(Base):
    __tablename__ = "run_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    row: Mapped[dict[str, Any]] = mapped_column(JSON)  # export row (brief section 4)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON)  # input line + match info
    run: Mapped[RunRow] = relationship(back_populates="lines")


class DocumentRow(Base):
    """A PO or sourcing request created when a buyer approves."""

    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # PO | RFQ | RFP | CAPEX | NEW_ITEM
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    group_ids: Mapped[list[str]] = mapped_column(JSON)
    supplier_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    suppliers_invited: Mapped[list[str]] = mapped_column(JSON, default=list)
    plant: Mapped[str] = mapped_column(String(10))
    value: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuditRow(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(60))
    comment: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
