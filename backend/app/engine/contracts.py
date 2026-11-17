"""Rate contract applicability and ceiling tracking (policy section 4)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .policy import Parameters
from .suppliers import is_allowed
from .types import Contract, MasterData


@dataclass
class ContractOption:
    contract: Contract
    rate: float
    value_at_rate: float
    remaining_before: float
    usable: bool

    @property
    def contract_id(self) -> str:
        return self.contract.contract_id


class CeilingLedger:
    """Consumed contract value, starting from the register (or zero for PDF-only contracts)."""

    def __init__(self, contracts: list[Contract]):
        self._consumed = {c.contract_id: c.consumed_value for c in contracts}

    def consumed(self, contract_id: str) -> float:
        return self._consumed.get(contract_id, 0.0)

    def consume(self, contract_id: str, value: float) -> None:
        self._consumed[contract_id] = self.consumed(contract_id) + value


def applicable_contracts(md: MasterData, item_code: str, plant: str, on: date, params: Parameters) -> list[Contract]:
    """4.1: covers the item, valid on the date (inclusive), supplier allowed, plant covered."""
    out = []
    for c in md.contracts_for_item(item_code):
        if not (c.start_date <= on <= c.end_date):
            continue
        if params.contracts.require_active_supplier and not is_allowed(md.suppliers.get(c.supplier_id), params):
            continue
        if c.plants and plant not in c.plants:
            continue
        out.append(c)
    return out


def contract_options(
    md: MasterData,
    ledger: CeilingLedger,
    item_code: str,
    plant: str,
    quantity: float,
    on: date,
    params: Parameters,
) -> list[ContractOption]:
    """Applicable contracts, cheapest first, each flagged usable if its ceiling has room (4.2/4.3)."""
    tolerance = 1 + params.contracts.ceiling_tolerance_pct / 100
    options = []
    for c in applicable_contracts(md, item_code, plant, on, params):
        rate = c.rate_for(item_code)
        value = round(quantity * rate, 2)
        remaining = c.value_cap - ledger.consumed(c.contract_id)
        options.append(ContractOption(c, rate, value, remaining, value <= remaining + c.value_cap * (tolerance - 1) + 1e-6))
    options.sort(key=lambda o: (o.rate, o.contract_id))
    return options
