"""Policy rules beyond the worked examples, and the data edge cases in the sample batch."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app.engine.amend import AmendmentError, apply_operations
from app.engine.matcher import ItemMatcher
from app.engine.pipeline import DecisionEngine
from app.engine.policy import PolicyBook
from app.engine.types import PRLine

BLOCKED_OR_INACTIVE = {"S004", "S008", "S011", "S031", "S034"}


def line(pr, item, qty, plant="PUN1", created="2026-11-10 10:00", need="2026-11-25", urgency="Normal",
         desc="", price=None, requester="T. Tester"):
    return PRLine(pr, 1, datetime.fromisoformat(created), requester, "Stores", plant, item, desc or item, qty, "",
                  price, date.fromisoformat(need), urgency)


def test_no_blocked_or_inactive_supplier_anywhere(sample_result):
    for r in sample_result.export_rows():
        used = {r["supplier_id"], *r["suggested_supplier_ids"].split(";")} - {""}
        assert not used & BLOCKED_OR_INACTIVE, r


def test_contract_with_blocked_supplier_is_ignored(rows):
    # RC-2026-005 (Metalink, Blocked) covers MS flat - must not be used
    r = rows[("PR-26-00024", 1)]
    assert r["route"] == "SPOT_PO" and r["supplier_id"] != "S004"


def test_expired_contract_is_ignored(rows):
    assert rows[("PR-26-00017", 1)]["route"] != "CONTRACT_PO"  # RC-2025-020 ended 30 Sep 2026


@pytest.mark.parametrize("pr, why", [("PR-26-00172", "quantity"), ("PR-26-00173", "plant"), ("PR-26-00175", "double submission")])
def test_not_processed_lines(rows, pr, why):
    r = rows[(pr, 1)]
    assert r["route"] == "NOT_PROCESSED" and why in r["reason"]


def test_every_line_has_a_version_and_reason(sample_result):
    for r in sample_result.export_rows():
        assert r["policy_version"] == "5.0" and r["reason"]


def test_consolidation_window_is_24h_from_first_requirement(rows):
    # S029/PUN1 contract POs on 13-14 Nov: 156, 157, 158 within 24h of 156; 159 is 26h57m later
    po = {pr: rows[(pr, 1)]["po_id"] for pr in ("PR-26-00156", "PR-26-00157", "PR-26-00158", "PR-26-00159")}
    assert po["PR-26-00156"] == po["PR-26-00157"] == po["PR-26-00158"] != po["PR-26-00159"]


def test_ceiling_consumed_in_requirement_order(master_data, policy_v5):
    # RC-2026-015 (Unbrako) has 12,000 left: first hex-bolt requirement that fits takes it
    eng = DecisionEngine(master_data, PolicyBook([policy_v5]))
    res = eng.process([
        line("A", "ITM-1009", 1000, created="2026-11-10 09:00"),  # 6,450 at 6.45 -> fits
        line("B", "ITM-1009", 1000, created="2026-11-10 10:00", need="2026-12-20"),  # 6,450 more -> does not fit
    ])
    a, b = (next(r for r in res.export_rows() if r["pr_no"] == x) for x in "AB")
    assert a["supplier_id"] == "S006" and a["unit_price"] == 6.45
    assert b["supplier_id"] == "S007" and b["unit_price"] == 7.06


def test_emergency_above_limit_goes_to_rfq(master_data, policy_v5):
    eng = DecisionEngine(master_data, PolicyBook([policy_v5]))
    res = eng.process([line("E", "ITM-1026", 6000, urgency="Emergency")])  # ~6.4 lakh
    assert res.export_rows()[0]["route"] == "RFQ"


def test_matcher_handles_short_forms_and_rejects_unknowns(master_data):
    m = ItemMatcher(master_data.items)
    assert m.match(None, "brg 6308 ZZ for maintenance").item_code == "ITM-1022"
    assert m.match(None, 'monitor 24"').item_code == "ITM-1057"
    assert m.match(None, "welding rod e6013 3.15mm").item_code == "ITM-1015"
    assert m.match(None, "pneu cylinder 50x100").item_code == "ITM-1036"
    assert m.match(None, "Drinking water purifier 50 LPH").status == "new"
    assert m.match(None, "Ultrasonic thickness gauge").status == "new"
    assert m.match(None, "Ball bearing 6309").status == "new"  # wrong size is not a match
    assert m.match(None, "gloves").status == "ambiguous"


# ----------------------------- policy as data --------------------------------
def test_amendment_changes_routes_from_its_effective_date(master_data, policy_v5, sample_lines):
    amended = apply_operations(
        policy_v5,
        [
            # spot limit raised to 3 lakh everywhere
            {"op": "update_rule", "rule_id": "R-5.4", "rule": {"when": {"all": [
                {"field": "value", "op": "lt", "value": 300000},
                {"field": "bought_recently", "op": "eq", "value": True}]}}},
            # new plant-only rule: at CHK1, Office & IT always goes to RFQ
            {"op": "add_rule", "before": "R-5.2", "rule": {
                "id": "R-5.1A", "clause": "5.1A", "title": "CHK1 Office & IT needs quotes",
                "when": {"all": [{"field": "plant", "op": "eq", "value": "CHK1"},
                                 {"field": "category", "op": "eq", "value": "Office & IT"}]},
                "outcome": {"decision": "SOURCING_REQUEST", "route": "RFQ", "supplier": "suggest", "suggest_count": 3}}},
        ],
        version="5.1", effective_from=date(2026, 11, 10),
    )
    res = DecisionEngine(master_data, PolicyBook([policy_v5, amended])).process(sample_lines)
    rows = {(r["pr_no"], r["pr_line"]): r for r in res.export_rows()}
    # dated before 10 Nov -> v5.0 still applies
    assert rows[("PR-26-00059", 1)]["policy_version"] == "5.0"
    # PR-09 taper bearings (12 Nov, 1.9 lakh, was SPOT) stays SPOT; PR-07 hex bolt M16 (6 Nov, 2.01 lakh) stays RFQ under 5.0
    assert rows[("PR-26-00007", 1)]["route"] == "RFQ"
    # PR-120 copper cable (18 Nov, 2.83 lakh, RFQ under 5.0) becomes a spot PO under 5.1
    assert rows[("PR-26-00120", 1)]["route"] == "SPOT_PO" and rows[("PR-26-00120", 1)]["policy_version"] == "5.1"
    # PR-43 monitors at CHK1 (14 Nov, was SPOT) now goes to RFQ by the plant rule
    r43 = rows[("PR-26-00043", 1)]
    assert r43["route"] == "RFQ" and "5.1A" in r43["reason"]


def test_override_parameter_for_one_plant(master_data, policy_v5):
    amended = apply_operations(policy_v5, [{"op": "add_override", "override": {
        "id": "OV-CHK1-CLUB", "description": "Chakan clubs over 30 days",
        "when": {"field": "plant", "op": "eq", "value": "CHK1"}, "set": {"clubbing.creation_window_days": 30}}}],
        version="5.1", effective_from=date(2026, 4, 1))
    lines = [line("A", "ITM-1026", 10, plant=p, created=c) for p in ("CHK1", "PUN1") for c in ("2026-11-01 10:00", "2026-11-20 10:00")]
    lines = [PRLine(f"{l.pr_no}{i}", 1, *list(vars(l).values())[2:]) for i, l in enumerate(lines)]
    res = DecisionEngine(master_data, PolicyBook([amended])).process(lines)
    groups = {(r["pr_no"]): r["group_id"] for r in res.export_rows()}
    assert groups["A0"] == groups["A1"]  # CHK1, 19 days apart, clubbed
    assert groups["A2"] != groups["A3"]  # PUN1, default 14 days


def test_invalid_amendment_is_rejected(policy_v5):
    with pytest.raises(AmendmentError):
        apply_operations(policy_v5, [{"op": "set_parameter", "path": "clubbing.no_such_thing", "value": 1}], "5.1", date(2026, 1, 1))
    with pytest.raises(AmendmentError):
        apply_operations(policy_v5, [{"op": "add_rule", "rule": {
            "id": "X", "clause": "X", "title": "bad", "when": {"field": "colour", "op": "eq", "value": "red"},
            "outcome": {"decision": "PO", "route": "SPOT_PO", "supplier": "preferred"}}}], "5.1", date(2026, 1, 1))
