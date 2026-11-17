"""End-to-end through the HTTP API on a fresh database."""
from __future__ import annotations

import csv
import io


def _batch(kit) -> bytes:
    return (kit / "pr_batches" / "sample_pr_batch.csv").read_bytes()


def test_process_batch_json_and_csv(client, kit):
    r = client.post("/api/process-batch", files={"file": ("b.csv", _batch(kit), "text/csv")})
    assert r.status_code == 200
    body = r.json()
    assert len(body["decisions"]) == 186 and body["policy_versions"] == ["5.0"]

    r = client.post("/api/process-batch?format=csv", content=_batch(kit), headers={"Content-Type": "text/csv"})
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 186
    assert list(rows[0]) == ["pr_no", "pr_line", "resolved_item_code", "group_id", "po_id", "decision", "route",
                             "supplier_id", "unit_price", "group_value_inr", "suggested_supplier_ids",
                             "policy_version", "reason"]


def test_bad_batch_is_rejected(client):
    r = client.post("/api/process-batch", content=b"foo,bar\n1,2\n", headers={"Content-Type": "text/csv"})
    assert r.status_code == 400 and "missing columns" in r.json()["detail"]


def test_policy_amendment_lifecycle_and_reprocess(client, kit):
    run1 = client.post("/api/process-batch", files={"file": ("b.csv", _batch(kit), "text/csv")}).json()
    draft = client.post("/api/policies/drafts/operations", json={
        "effective_from": "2026-11-15", "summary": "Spot limit 3 lakh",
        "operations": [{"op": "update_rule", "rule_id": "R-5.4", "rule": {"when": {"all": [
            {"field": "value", "op": "lt", "value": 300000},
            {"field": "bought_recently", "op": "eq", "value": True}]}}}],
    }).json()
    assert draft["status"] == "draft" and draft["version"] == "5.1"
    assert any("5.4" in c["text"] and "3,00,000" in c["text"] for c in draft["changes"])

    # nothing changes until approval
    again = client.post(f"/api/runs/{run1['run_id']}/reprocess").json()
    assert again["policy_versions"] == ["5.0"]

    approved = client.post(f"/api/policies/{draft['id']}/approve", json={"comment": "OK per CPO"}).json()
    assert approved["status"] == "active" and approved["decided_by"] == "Test Buyer"

    run2 = client.post(f"/api/runs/{run1['run_id']}/reprocess").json()
    assert run2["policy_versions"] == ["5.0", "5.1"]
    lines = {(l["row"]["pr_no"], l["row"]["pr_line"]): l["row"] for l in client.get(f"/api/runs/{run2['id']}").json()["lines"]}
    assert lines[("PR-26-00120", 1)]["route"] == "SPOT_PO"  # 18 Nov, 2.83 lakh
    assert lines[("PR-26-00007", 1)]["route"] == "RFQ"  # 6 Nov -> still v5.0

    audit = [a["action"] for a in client.get("/api/audit").json()]
    assert "policy.draft" in audit and "policy.approve" in audit


def test_policy_reader_needs_ai_and_says_so(client):
    r = client.post("/api/policies/drafts/chat", json={"text": "Raise the spot limit to 3 lakh"})
    assert r.status_code == 503 and "structured editor" in r.json()["detail"]


def test_approve_override_and_audit(client, kit):
    run = client.post("/api/process-batch", files={"file": ("b.csv", _batch(kit), "text/csv")}).json()
    rid = run["run_id"]
    detail = client.get(f"/api/runs/{rid}").json()
    hex_req = next(r for r in detail["requirements"] if r["po_id"] and r["item_code"] == "ITM-1009" and r["plant"] == "PUN1")
    doc = client.post(f"/api/runs/{rid}/requirements/{hex_req['group_id']}/approve", json={}).json()
    assert doc["kind"] == "PO" and len(doc["group_ids"]) == 2  # both requirements consolidated on the PO

    rfq = next(r for r in detail["requirements"] if r["route"] == "RFQ")
    no_comment = client.post(f"/api/runs/{rid}/requirements/{rfq['group_id']}/override",
                             json={"route": "RFP", "comment": ""})
    assert no_comment.status_code == 422
    blocked = client.post(f"/api/runs/{rid}/requirements/{rfq['group_id']}/override",
                          json={"route": "SPOT_PO", "comment": "use metalink", "supplier_id": "S004", "unit_price": 10})
    assert blocked.status_code == 422 and "Blocked" in blocked.json()["detail"]
    ok = client.post(f"/api/runs/{rid}/requirements/{rfq['group_id']}/override",
                     json={"route": "RFP", "comment": "Specification is complex, need technical bids",
                           "suggested_supplier_ids": ["S014", "S015"]})
    assert ok.status_code == 200
    hist = client.get(f"/api/runs/{rid}/requirements/{rfq['group_id']}/history").json()
    assert hist[0]["action"] == "decision.override" and hist[0]["details"]["original"]["route"] == "RFQ"
    export = client.get(f"/api/runs/{rid}/export.csv?include_overrides=true").text
    assert "OVERRIDDEN by Test Buyer" in export


def test_preferred_supplier_export(client):
    text = client.get("/api/preferred-suppliers/export.csv?as_of=2026-11-01").text
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0]) == ["item_code", "rank", "supplier_id", "reference_price"]
    gloves = [r["supplier_id"] for r in rows if r["item_code"] == "ITM-1048"]
    assert gloves == ["S029", "S030"]
    assert all(int(r["rank"]) <= 3 for r in rows)


def test_contract_pdf_extract_and_add(client, kit):
    pdf = (kit / "contracts" / "pdf" / "RC-2026-033.pdf").read_bytes()
    ex = client.post("/api/contracts/extract", files={"file": ("RC-2026-033.pdf", pdf, "application/pdf")}).json()
    assert ex["contract_id"] == "RC-2026-033" and ex["value_cap"] == 5000000 and len(ex["lines"]) == 2
    assert ex["already_registered"]  # imported at setup
    dup = client.post("/api/contracts", json={**ex, "source": "pdf"})
    assert dup.status_code == 409
    new = {**ex, "contract_id": "RC-2026-099", "source": "manual"}
    assert client.post("/api/contracts", json=new).status_code == 200


def _chat(client, messages):
    sid, out = None, []
    for text, option in messages:
        r = client.post("/api/chat", json={"session_id": sid, "message": text, "option": option}).json()
        sid = r["session_id"]
        out.append(r)
    return out


def test_chat_creates_pr_in_one_go(client):
    replies = _chat(client, [("need 200 nitrile gloves for Chakan by next Friday", None), ("normal", None), ("yes", None)])
    assert "ITM-1048" in replies[0]["text"]
    assert replies[-1]["pr"]["pr_no"].startswith("PR-C-")
    prs = client.get("/api/prs").json()
    assert prs[0]["item_code"] == "ITM-1048" and prs[0]["quantity"] == 200 and prs[0]["plant"] == "CHK1"


def test_chat_filler_is_not_searched_as_an_item(client):
    r = _chat(client, [("add one item", None)])[0]
    assert "What do you need" in r["text"] and not r["options"]


def test_chat_add_another_item_after_pr(client):
    replies = _chat(client, [("need 200 nitrile gloves for Chakan by next Friday", None), ("normal", None),
                             ("yes", None), ("add another item", None), ("50 safety helmet isi", None)])
    assert "Adding line 2" in replies[3]["text"]
    assert replies[4]["draft"]["item_code"] == "ITM-1049" and replies[4]["draft"]["line_no"] == 2
    assert replies[4]["draft"]["plant"] == "CHK1"  # plant carries over to the next line of the same PR


def test_chat_single_suggestion_accepts_yes_and_ignores_unrelated_no(client):
    first, unrelated, yes = _chat(client, [("3 reams a4 paper for bhosari by 30 nov", None),
                                           ("no, routine", None), ("yes", None)])
    assert "do you mean" in first["text"] and "ITM-1058" in first["text"]
    assert unrelated["draft"]["item_code"] is None  # "no, routine" did not turn it into a new item
    assert yes["draft"]["item_code"] == "ITM-1058"


def test_chat_asks_follow_ups_and_does_not_invent_items(client):
    r = _chat(client, [("gloves", None)])[0]
    assert {o["value"] for o in r["options"]} >= {"item:ITM-1047", "item:ITM-1048", "new"}

    replies = _chat(client, [("ultrasonic thickness gauge", None)])
    assert "couldn't find" in replies[0]["text"] and not any(o["value"].startswith("item:") for o in replies[0]["options"])

    flow = _chat(client, [("need hydraulic oil hlp 68", None), ("300", None), ("PUN1", None), ("30 dec", None),
                          ("machine breakdown", None), ("yes", None)])
    assert "How many" in flow[0]["text"]
    assert "Which plant" in flow[1]["text"]
    assert "When do you need" in flow[2]["text"]
    assert flow[-1]["draft"]["urgency"] == "Emergency"
