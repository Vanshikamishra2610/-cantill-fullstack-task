"""Extract a rate contract from its signed PDF.

The deterministic parser handles Corvo's standard contract template. If a PDF
does not follow it, the service falls back to the LLM extractor (when an API
key is configured). Either way the buyer reviews the fields before saving.
"""
from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field

from pypdf import PdfReader


@dataclass
class ExtractedLine:
    item_code: str
    description: str
    uom: str
    rate: float


@dataclass
class ExtractedContract:
    contract_id: str = ""
    supplier_id: str = ""
    supplier_name: str = ""
    start_date: str = ""
    end_date: str = ""
    value_cap: float = 0.0
    plants: list[str] = field(default_factory=list)
    lines: list[ExtractedLine] = field(default_factory=list)
    method: str = "template"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def complete(self) -> bool:
        return all([self.contract_id, self.supplier_id, self.start_date, self.end_date, self.value_cap, self.lines])


def pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _amount(s: str) -> float:
    return float(s.replace(",", ""))


def parse_contract_text(text: str) -> ExtractedContract:
    flat = re.sub(r"\s+", " ", text)
    out = ExtractedContract()
    if m := re.search(r"Contract No\.?\s*([A-Z]{2}-\d{4}-\d{3,})", flat):
        out.contract_id = m.group(1)
    if m := re.search(r"and (.+?),\s*[A-Za-z ]+\(Supplier ID\s*(S\d+)\)", flat):
        out.supplier_name, out.supplier_id = m.group(1).strip(), m.group(2)
    if m := re.search(r"valid from (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", flat):
        out.start_date, out.end_date = m.group(1), m.group(2)
    if m := re.search(r"shall not exceed INR\s*([\d,]+(?:\.\d+)?)", flat):
        out.value_cap = _amount(m.group(1))
    plants = re.findall(r"\b(PUN\d|CHK\d)\b", flat)
    out.plants = sorted(set(plants))

    for m in re.finditer(r"^\s*\d+\s+(ITM-\d+)\s+(.+?)\s+([A-Z]{2,5})\s+([\d,]+\.\d{2})\s*$", text, re.MULTILINE):
        out.lines.append(ExtractedLine(m.group(1), m.group(2).strip(), m.group(3), _amount(m.group(4))))

    for name, value in [("contract number", out.contract_id), ("supplier ID", out.supplier_id),
                        ("validity dates", out.start_date), ("value ceiling", out.value_cap), ("rate schedule", out.lines)]:
        if not value:
            out.warnings.append(f"could not find the {name}")
    return out


def extract_contract(data: bytes) -> ExtractedContract:
    return parse_contract_text(pdf_text(data))
