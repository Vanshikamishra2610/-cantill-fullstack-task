"""The 14 worked decisions and 3 preferred-supplier lists from Worked_Examples.pdf.

PO numbers are compared as *groupings* (which requirements share a PO), not as
literal ids: see DECISIONS.md on why our numbering differs by one.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.engine.suppliers import build_preferred_list


def row(rows, pr):
    return rows[(pr, 1)]


@pytest.mark.parametrize(
    "pr, route, supplier, price, value",
    [
        ("PR-26-00019", "CONTRACT_PO", "S001", 9168.71, 137530.65),   # 1 contract PO
        ("PR-26-00045", "CONTRACT_PO", "S021", 2439.01, 26829.11),    # 2 PDF-only contract
        ("PR-26-00034", "CONTRACT_PO", "S007", 7.06, 32546.6),        # 4 two contracts, lowest with ceiling
        ("PR-26-00146", "SPOT_PO", "S024", 166.41, 126471.6),         # 5 clubbed split PRs
        ("PR-26-00106", "SPOT_PO", "S027", 21.59, 156170.3),          # 6 same item, other plant
        ("PR-26-00088", "SPOT_PO", "S023", 391.82, 97955.0),          # 7 free-text match
        ("PR-26-00059", "EMERGENCY_PO", "S014", 106.38, 253184.4),    # 8 emergency PO
    ],
)
def test_po_examples(rows, pr, route, supplier, price, value):
    r = row(rows, pr)
    assert (r["decision"], r["route"], r["supplier_id"]) == ("PO", route, supplier)
    assert r["unit_price"] == pytest.approx(price, abs=0.01)
    assert r["group_value_inr"] == pytest.approx(value, abs=0.5)
    assert r["po_id"]


@pytest.mark.parametrize(
    "pr, route, suggested, value",
    [
        ("PR-26-00004", "RFQ", "S014;S013;S016", 220255.6),                   # 3 ceiling nearly used
        ("PR-26-00089", "RFQ", "S043;S044;S042", 336562.15),                  # 9 above PO limit
        ("PR-26-00050", "RFQ", "S003;S002;S005", 80300.0),                    # 10 small, never bought
        ("PR-26-00100", "RFP", "S020;S017;S019;S018", 2070780.0),             # 11 RFP
        ("PR-26-00047", "CAPEX_SOURCING", "S045;S047;S020;S048;S046", 6036704.08),  # 12 capex
        ("PR-26-00170", "NEW_ITEM_SOURCING", "", 141100.0),                   # 13 new item
    ],
)
def test_sourcing_examples(rows, pr, route, suggested, value):
    r = row(rows, pr)
    assert (r["decision"], r["route"]) == ("SOURCING_REQUEST", route)
    assert r["suggested_supplier_ids"] == suggested
    assert r["group_value_inr"] == pytest.approx(value, abs=0.5)
    assert r["po_id"] == "" and r["supplier_id"] == ""


def test_clubbing_example_5(rows):
    groups = {row(rows, pr)["group_id"] for pr in ("PR-26-00144", "PR-26-00145", "PR-26-00146")}
    assert len(groups) == 1


def test_example_3_free_text_and_coded_line_clubbed(rows):
    a, b = row(rows, "PR-26-00004"), row(rows, "PR-26-00026")
    assert a["resolved_item_code"] == "ITM-1022" and a["group_id"] == b["group_id"]


def test_example_6_different_plants_not_clubbed(rows):
    a, b = row(rows, "PR-26-00106"), row(rows, "PR-26-00103")
    assert a["group_id"] != b["group_id"]
    assert b["resolved_item_code"] == "ITM-1044"


def test_example_14_requirements_consolidated_on_one_po(rows):
    hex_bolt = [row(rows, pr) for pr in ("PR-26-00031", "PR-26-00035", "PR-26-00034", "PR-26-00032")]
    assert len({r["group_id"] for r in hex_bolt}) == 2  # two requirements (need-by dates 13 days apart)
    assert len({r["po_id"] for r in hex_bolt}) == 1  # ...on one PO
    assert sorted({r["group_value_inr"] for r in hex_bolt}) == pytest.approx([23227.4, 32546.6])


def test_pdf_only_contract_not_used_before_its_start(rows):
    # FRL unit PR-36 is dated 14 Nov, RC-2026-031 starts 15 Nov
    assert row(rows, "PR-26-00036")["route"] == "SPOT_PO"
    assert row(rows, "PR-26-00040")["supplier_id"] == "S021"  # 20 Nov, contract applies


def _ranking(master_data, policy, item, on):
    return [e.supplier_id for e in build_preferred_list(master_data, item, on, policy.parameters)]


def test_preferred_list_nitrile_gloves(master_data, policy_v5):
    assert _ranking(master_data, policy_v5, "ITM-1048", date(2026, 11, 1)) == ["S029", "S030"]  # S031 Inactive


def test_preferred_list_pneumatic_cylinder(master_data, policy_v5):
    assert _ranking(master_data, policy_v5, "ITM-1036", date(2026, 11, 1)) == ["S023", "S022", "S024"]


def test_preferred_list_mig_wire(master_data, policy_v5):
    assert _ranking(master_data, policy_v5, "ITM-1016", date(2026, 11, 1)) == ["S009", "S010"]


def test_reference_price_falls_back_to_contract(master_data, policy_v5):
    entries = build_preferred_list(master_data, "ITM-1036", date(2026, 11, 20), policy_v5.parameters)
    s021 = next(e for e in entries if e.supplier_id == "S021")
    assert s021.holds_contract and s021.reference_source.startswith(("PO", "contract"))
