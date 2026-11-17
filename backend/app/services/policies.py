"""Policy versions: drafts, plain-language review, approval and activation."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..engine.amend import AmendmentError, apply_operations
from ..engine.describe import describe_policy, diff_policies
from ..engine.policy import Policy, PolicyBook
from ..models import PolicyVersionRow, utcnow
from .master import audit
from .policy_reader import next_version, read_amendment


class PolicyError(ValueError):
    pass


def to_policy(row: PolicyVersionRow) -> Policy:
    return Policy.model_validate(row.document)


def active_rows(db: Session) -> list[PolicyVersionRow]:
    return list(db.scalars(select(PolicyVersionRow).where(PolicyVersionRow.status == "active").order_by(PolicyVersionRow.id)))


def policy_book(db: Session) -> PolicyBook:
    return PolicyBook([to_policy(r) for r in active_rows(db)])


def latest_active(db: Session) -> PolicyVersionRow:
    rows = active_rows(db)
    if not rows:
        raise PolicyError("no active policy version")
    return sorted(rows, key=lambda r: r.effective_from)[-1]


def serialize(row: PolicyVersionRow, with_document: bool = False) -> dict[str, Any]:
    out = {
        "id": row.id,
        "version": row.version,
        "effective_from": row.effective_from.isoformat(),
        "status": row.status,
        "based_on_id": row.based_on_id,
        "source_kind": row.source_kind,
        "source_name": row.source_name,
        "summary": row.document.get("summary", ""),
        "changes": row.changes,
        "warnings": row.warnings,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat(),
        "decided_by": row.decided_by,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_comment": row.decision_comment,
    }
    if with_document:
        out["document"] = row.document
        out["source_text"] = row.source_text
        out["plain"] = describe_policy(to_policy(row))
    return out


def _unique_version(db: Session, version: str) -> str:
    taken = {v for v in db.scalars(select(PolicyVersionRow.version).where(PolicyVersionRow.status != "rejected"))}
    candidate = version
    while candidate in taken:
        candidate = next_version(candidate)
    return candidate


def _store_draft(db: Session, base_row: PolicyVersionRow, policy: Policy, actor: str, source_kind: str,
                 source_name: str, source_text: str, warnings: list[str], explanations: list[str]) -> PolicyVersionRow:
    version = _unique_version(db, policy.version)
    if version != policy.version:
        warnings = [*warnings, f"Version {policy.version} already exists; this draft is numbered {version}."]
        policy = policy.model_copy(update={"version": version})
    changes = diff_policies(to_policy(base_row), policy)
    if explanations:
        changes = [*changes, *({"kind": "explanation", "text": e} for e in explanations)]
    row = PolicyVersionRow(
        version=policy.version, effective_from=policy.effective_from, status="draft",
        document=policy.model_dump(mode="json", by_alias=True), based_on_id=base_row.id,
        source_kind=source_kind, source_name=source_name, source_text=source_text,
        changes=changes, warnings=warnings, created_by=actor,
    )
    db.add(row)
    db.flush()
    audit(db, actor, "policy.draft", "policy", row.version, f"Draft created from {source_kind}", draft_id=row.id)
    db.commit()
    return row


def draft_from_text(db: Session, text: str, actor: str, source_kind: str, source_name: str = "") -> PolicyVersionRow:
    """Amendment document or chat description -> draft (needs the LLM)."""
    base_row = latest_active(db)
    result = read_amendment(text, to_policy(base_row), date.today())
    return _store_draft(db, base_row, result.policy, actor, source_kind, source_name, text, result.warnings, result.explanations)


def draft_from_operations(db: Session, operations: list[dict[str, Any]], version: str | None,
                          effective_from: date, summary: str, actor: str) -> PolicyVersionRow:
    base_row = latest_active(db)
    base = to_policy(base_row)
    try:
        policy = apply_operations(base, operations, version or next_version(base.version), effective_from, summary)
    except AmendmentError as exc:
        raise PolicyError(str(exc)) from exc
    return _store_draft(db, base_row, policy, actor, "editor", "structured editor", "", [], [])


def draft_from_document(db: Session, document: dict[str, Any], actor: str) -> PolicyVersionRow:
    """A full policy JSON edited by hand."""
    base_row = latest_active(db)
    try:
        policy = Policy.model_validate(document)
    except ValueError as exc:
        raise PolicyError(f"policy JSON is invalid: {exc}") from exc
    return _store_draft(db, base_row, policy, actor, "editor", "JSON editor", "", [], [])


def approve(db: Session, row: PolicyVersionRow, actor: str, effective_from: date | None, comment: str) -> PolicyVersionRow:
    if row.status != "draft":
        raise PolicyError(f"version {row.version} is {row.status}, only drafts can be approved")
    if effective_from and effective_from != row.effective_from:
        doc = dict(row.document)
        doc["effective_from"] = effective_from.isoformat()
        row.document = Policy.model_validate(doc).model_dump(mode="json", by_alias=True)
        row.effective_from = effective_from
    row.status, row.decided_by, row.decided_at, row.decision_comment = "active", actor, utcnow(), comment
    audit(db, actor, "policy.approve", "policy", row.version, comment,
          effective_from=row.effective_from.isoformat(), draft_id=row.id)
    db.commit()
    return row


def reject(db: Session, row: PolicyVersionRow, actor: str, comment: str) -> PolicyVersionRow:
    if row.status != "draft":
        raise PolicyError("only drafts can be rejected")
    row.status, row.decided_by, row.decided_at, row.decision_comment = "rejected", actor, utcnow(), comment
    audit(db, actor, "policy.reject", "policy", row.version, comment, draft_id=row.id)
    db.commit()
    return row


def withdraw(db: Session, row: PolicyVersionRow, actor: str, comment: str) -> PolicyVersionRow:
    if row.status != "active":
        raise PolicyError("only active versions can be withdrawn")
    if len(active_rows(db)) == 1:
        raise PolicyError("cannot withdraw the only active version")
    if not comment.strip():
        raise PolicyError("a reason is required to withdraw a version")
    row.status, row.decided_by, row.decided_at, row.decision_comment = "withdrawn", actor, utcnow(), comment
    audit(db, actor, "policy.withdraw", "policy", row.version, comment)
    db.commit()
    return row
