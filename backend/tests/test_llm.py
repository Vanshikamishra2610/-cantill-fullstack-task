"""LLM provider selection and the OpenAI-compatible (Gemini) client, with HTTP faked."""
from __future__ import annotations

import importlib
import json

import httpx
import pytest

SCHEMA = {"type": "object", "properties": {"answer": {"type": ["string", "null"]}}, "required": ["answer"]}
AI_VARS = ["LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL", "GEMINI_API_KEY",
           "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL", "LLM_ENABLED"]


@pytest.fixture()
def llm(monkeypatch):
    for var in AI_VARS:
        monkeypatch.delenv(var, raising=False)
    return importlib.import_module("app.services.llm")


def reply(content: str, status: int = 200, finish: str = "stop") -> httpx.Response:
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    return httpx.Response(status, json=body)


def test_no_key_means_ai_off(llm):
    assert not llm.llm_available() and llm.provider_label() == "off"


def test_gemini_key_selects_gemini(llm, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    s = llm.settings()
    assert (s.provider, s.base_url) == ("gemini", llm.GEMINI_BASE_URL)
    assert s.model.startswith("gemini-flash-lite-latest,")
    assert llm.llm_available() and llm.provider_label() == "gemini (gemini-flash-lite-latest)"
    monkeypatch.setenv("LLM_MODEL", "gemini-3.8-flash")
    assert llm.settings().model == "gemini-3.8-flash"


def test_anthropic_key_wins_when_both_set(llm, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    assert llm.settings().provider == "anthropic"
    monkeypatch.setenv("LLM_PROVIDER", "gemini")  # explicit choice overrides
    assert llm.settings().provider == "gemini"


def test_gemini_call_sends_schema_and_parses_json(llm, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    calls = []

    def fake_post(url, headers, payload):
        calls.append((url, headers, payload))
        return reply('{"answer": "42"}')

    monkeypatch.setattr(llm, "_http_post", fake_post)
    assert llm.extract_json("sys", "question", SCHEMA) == {"answer": "42"}
    url, headers, payload = calls[0]
    assert url == llm.GEMINI_BASE_URL + "chat/completions"
    assert headers["Authorization"] == "Bearer g-key"
    assert payload["response_format"]["type"] == "json_schema" and payload["model"] == "gemini-flash-lite-latest"


def test_falls_back_to_looser_format_and_strips_code_fences(llm, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    formats = []

    def fake_post(url, headers, payload):
        formats.append(payload.get("response_format", {}).get("type"))
        if len(formats) == 1:
            return httpx.Response(400, text="schema not supported")
        return reply('Here you go:\n```json\n{"answer": null}\n```')

    monkeypatch.setattr(llm, "_http_post", fake_post)
    assert llm.extract_json("sys", "q", SCHEMA) == {"answer": None}
    assert formats == ["json_schema", "json_object"]


def test_retired_or_overloaded_model_falls_back_to_next(llm, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("LLM_MODEL", "old-model, busy-model, good-model")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    seen = []

    def fake_post(url, headers, payload):
        seen.append(payload["model"])
        if payload["model"] == "old-model":
            return httpx.Response(404, json=[{"error": {"message": "no longer available to new users"}}])
        if payload["model"] == "busy-model":
            return httpx.Response(503, json={"error": {"message": "high demand"}})
        return reply('{"answer": "ok"}')

    monkeypatch.setattr(llm, "_http_post", fake_post)
    assert llm.extract_json("sys", "q", SCHEMA) == {"answer": "ok"}
    assert seen == ["old-model", "busy-model", "busy-model", "busy-model", "good-model"]  # 503 retried twice


def test_all_models_down_is_reported(llm, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("LLM_MODEL", "a,b")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "_http_post", lambda *a: httpx.Response(503, json={"error": {"message": "busy"}}))
    with pytest.raises(llm.LLMUnavailable, match="no gemini model is available"):
        llm.extract_json("sys", "q", SCHEMA)


@pytest.mark.parametrize("response, message", [
    (httpx.Response(429, text="quota"), "rate limit"),
    (httpx.Response(403, text="bad key"), "rejected the API key"),
    (reply("not json at all"), "valid JSON"),
    (reply('{"other": 1}'), "missing answer"),
    (reply('{"answer": "x"', finish="length"), "cut off"),
])
def test_failures_become_llm_unavailable(llm, monkeypatch, response, message):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setattr(llm, "_http_post", lambda *a: response)
    with pytest.raises(llm.LLMUnavailable, match=message):
        llm.extract_json("sys", "q", SCHEMA)


def test_policy_change_described_in_chat_via_gemini(client, monkeypatch):
    """End to end: a described change -> Gemini (faked) -> validated draft with a plain-language diff."""
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    llm = importlib.import_module("app.services.llm")
    rule = {"id": "R-5.4", "clause": "5.4", "title": "Below 3,00,000 with recent purchase history",
            "when": {"all": [{"field": "value", "op": "lt", "value": 300000},
                             {"field": "bought_recently", "op": "eq", "value": True}]},
            "outcome": {"decision": "PO", "route": "SPOT_PO", "supplier": "preferred"}}
    answer = {
        "version": "5.1", "effective_from": "2026-11-15", "summary": "Spot limit raised to 3 lakh",
        "operations": [{"op": "update_rule", "path": None, "value_json": None, "rule_id": "R-5.4",
                        "override_id": None, "before": None, "after": None, "rule_json": json.dumps(rule),
                        "override_json": None, "explanation": "Spot POs are now allowed below 3 lakh."}],
        "unmapped": [],
    }
    monkeypatch.setattr(llm, "_http_post", lambda *a: reply(json.dumps(answer)))

    assert client.get("/api/health").json()["ai_provider"] == "gemini (gemini-flash-lite-latest)"
    r = client.post("/api/policies/drafts/chat", json={"text": "From 15 Nov the spot PO limit is 3 lakh (v5.1)"})
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["status"] == "draft" and draft["version"] == "5.1" and draft["effective_from"] == "2026-11-15"
    assert any("3,00,000" in c["text"] for c in draft["changes"])


def test_chat_still_asks_urgency_when_model_guesses_normal(client, monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    llm = importlib.import_module("app.services.llm")
    guessed = {"item_text": "nitrile gloves", "quantity": 200, "uom": None, "plant": "CHK1",
               "need_by_date": "2026-12-30", "urgency": "Normal", "estimated_unit_price": None, "intent": "inform"}
    monkeypatch.setattr(llm, "_http_post", lambda *a: reply(json.dumps(guessed)))
    r = client.post("/api/chat", json={"message": "need 200 nitrile gloves for Chakan by 30 Dec"}).json()
    assert "emergency" in r["text"].lower() and r["draft"]["urgency"] is None
