"""Plain domain types used by the decision engine.

The engine never touches the database: callers load master data into these
immutable objects (see ``MasterData``) and pass them in. That keeps the engine
pure, deterministic and easy to unit test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Item:
    item_code: str
    description: str
    uom: str
    category: str
    item_type: str  # Material | Service | Capex
    standard_price: float


@dataclass(frozen=True)
class Supplier:
    supplier_id: str
    name: str
    city: str
    categories: tuple[str, ...]
    status: str  # Active | Inactive | Blocked
    on_time_pct: float
    quality_rating: float


@dataclass(frozen=True)
class PurchaseOrder:
    po_number: str
    po_date: date
    supplier_id: str
    item_code: str
    quantity: float
    uom: str
    unit_price: float
    plant: str
    delivered_on_time: bool


@dataclass(frozen=True)
class ContractLine:
    item_code: str
    description: str
    uom: str
    rate: float


@dataclass(frozen=True)
class Contract:
    contract_id: str
    supplier_id: str
    supplier_name: str
    start_date: date
    end_date: date
    value_cap: float
    consumed_value: float
    lines: tuple[ContractLine, ...]
    plants: tuple[str, ...] = ()  # empty = all plants
    source: str = "register"

    def rate_for(self, item_code: str) -> float | None:
        for line in self.lines:
            if line.item_code == item_code:
                return line.rate
        return None


@dataclass(frozen=True)
class PRLine:
    pr_no: str
    pr_line: int
    created_at: datetime | None
    requester: str
    department: str
    plant: str
    item_code: str
    description: str
    quantity: float | None
    uom: str
    estimated_unit_price: float | None
    need_by_date: date | None
    urgency: str
    raw_errors: tuple[str, ...] = ()  # parse problems found while reading input

    @property
    def key(self) -> tuple[str, int]:
        return (self.pr_no, self.pr_line)

    @property
    def sort_key(self) -> tuple:
        return (self.created_at or datetime.max, self.pr_no, self.pr_line)

    @property
    def is_emergency(self) -> bool:
        return (self.urgency or "").strip().lower() == "emergency"


@dataclass
class MasterData:
    items: dict[str, Item]
    suppliers: dict[str, Supplier]
    purchase_orders: list[PurchaseOrder]
    contracts: list[Contract]
    plants: dict[str, str]
    # indexes built lazily
    _po_by_item: dict[str, list[PurchaseOrder]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        index: dict[str, list[PurchaseOrder]] = {}
        for po in self.purchase_orders:
            index.setdefault(po.item_code, []).append(po)
        for pos in index.values():
            pos.sort(key=lambda p: (p.po_date, p.po_number))
        self._po_by_item = index

    def pos_for_item(self, item_code: str) -> list[PurchaseOrder]:
        """POs for an item, oldest first."""
        return self._po_by_item.get(item_code, [])

    def contracts_for_item(self, item_code: str) -> list[Contract]:
        return [c for c in self.contracts if c.rate_for(item_code) is not None]
