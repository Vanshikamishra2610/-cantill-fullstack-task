"""Reading PR batch CSVs and writing the decision export (brief section 4)."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any, Iterable

from dateutil import parser as dateparser

from .types import PRLine

PR_COLUMNS = [
    "pr_no", "pr_line", "created_at", "requester", "department", "plant", "item_code",
    "description", "quantity", "uom", "estimated_unit_price", "need_by_date", "urgency",
]

EXPORT_COLUMNS = [
    "pr_no", "pr_line", "resolved_item_code", "group_id", "po_id", "decision", "route",
    "supplier_id", "unit_price", "group_value_inr", "suggested_supplier_ids", "policy_version", "reason",
]


class BatchFormatError(ValueError):
    pass


def _parse_datetime(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return dateparser.parse(value, dayfirst=False)


def _parse_date(value: str) -> date | None:
    dt = _parse_datetime(value)
    return dt.date() if dt else None


def _parse_float(value: str) -> float | None:
    value = (value or "").replace(",", "").strip()
    return float(value) if value else None


def parse_pr_rows(rows: Iterable[dict[str, Any]]) -> list[PRLine]:
    lines: list[PRLine] = []
    for idx, row in enumerate(rows, start=2):  # header is row 1
        row = {(k or "").strip().lower(): ("" if v is None else str(v)) for k, v in row.items()}
        errors: list[str] = []

        def safe(fn, field: str):
            try:
                return fn(row.get(field, ""))
            except (ValueError, OverflowError, dateparser.ParserError):
                errors.append(f"{field} '{row.get(field)}' could not be read")
                return None

        try:
            pr_line = int(float(row.get("pr_line") or 1))
        except ValueError:
            errors.append(f"pr_line '{row.get('pr_line')}' could not be read")
            pr_line = 0
        lines.append(
            PRLine(
                pr_no=row.get("pr_no", "").strip() or f"ROW-{idx}",
                pr_line=pr_line,
                created_at=safe(_parse_datetime, "created_at"),
                requester=row.get("requester", "").strip(),
                department=row.get("department", "").strip(),
                plant=row.get("plant", "").strip().upper(),
                item_code=row.get("item_code", "").strip().upper(),
                description=row.get("description", "").strip(),
                quantity=safe(_parse_float, "quantity"),
                uom=row.get("uom", "").strip().upper(),
                estimated_unit_price=safe(_parse_float, "estimated_unit_price"),
                need_by_date=safe(_parse_date, "need_by_date"),
                urgency=(row.get("urgency", "").strip() or "Normal").title(),
                raw_errors=tuple(errors),
            )
        )
    return lines


def parse_pr_csv(text: str) -> list[PRLine]:
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    header = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    missing = {"pr_no", "created_at", "plant", "description", "quantity", "need_by_date"} - header
    if missing:
        raise BatchFormatError(f"PR batch CSV is missing columns: {', '.join(sorted(missing))}")
    return parse_pr_rows(reader)


def rows_to_csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()
