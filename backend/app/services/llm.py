"""Structured JSON extraction from an LLM, with a choice of provider.

The LLM is only ever asked to *read* text (an amendment, a chat message, a
contract PDF) into JSON. All decisions are made by the deterministic engine,
and every JSON it returns is validated by the caller before use.

Providers (environment variable ``LLM_PROVIDER``):

* ``anthropic`` (default when ANTHROPIC_API_KEY is set) - Claude via the Anthropic SDK.
* ``gemini`` - Google Gemini, which has a free tier, through its
  OpenAI-compatible endpoint. Needs ``GEMINI_API_KEY`` (or ``LLM_API_KEY``).
* ``openai_compatible`` - any endpoint that speaks the OpenAI chat-completions
  format (Groq, OpenRouter, ...). Needs ``LLM_BASE_URL``, ``LLM_API_KEY``, ``LLM_MODEL``.

``LLM_MODEL`` overrides the model for any provider; it may be a comma-separated
list, tried in order when a model is retired (404) or overloaded (503).
``LLM_ENABLED=false`` turns AI off.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# Gemini retires models for new keys quickly, so use "-latest" aliases (they follow the
# current release). Flash-Lite first: fast and least often overloaded; the others are fallbacks.
DEFAULT_MODELS = {
    "anthropic": "claude-opus-5-5",
    "gemini": "gemini-flash-lite-latest,gemini-flash-latest,gemini-3.8-flash",
}
OVERLOAD_RETRIES = 2  # per model, with a short back-off


class LLMUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMSettings:
    provider: str  # anthropic | gemini | openai_compatible | none
    model: str
    api_key: str
    base_url: str


def settings() -> LLMSettings:
    """Current LLM configuration, read from the environment on every call."""
    if os.getenv("LLM_ENABLED", "true").lower() == "false":
        return LLMSettings("none", "", "", "")
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not provider:  # infer from whichever key is present
        if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
            provider = "anthropic"
        elif os.getenv("GEMINI_API_KEY"):
            provider = "gemini"
        elif os.getenv("LLM_API_KEY") and os.getenv("LLM_BASE_URL"):
            provider = "openai_compatible"
        else:
            return LLMSettings("none", "", "", "")

    if provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or ""
        model = os.getenv("LLM_MODEL") or os.getenv("ANTHROPIC_MODEL") or DEFAULT_MODELS["anthropic"]
        return LLMSettings("anthropic", model, key, "")
    if provider == "gemini":
        key = os.getenv("GEMINI_API_KEY") or os.getenv("LLM_API_KEY") or ""
        model = os.getenv("LLM_MODEL") or DEFAULT_MODELS["gemini"]
        return LLMSettings("gemini", model, key, os.getenv("LLM_BASE_URL") or GEMINI_BASE_URL)
    if provider == "openai_compatible":
        return LLMSettings("openai_compatible", os.getenv("LLM_MODEL", ""), os.getenv("LLM_API_KEY", ""),
                           os.getenv("LLM_BASE_URL", ""))
    log.warning("Unknown LLM_PROVIDER '%s'; AI features are off", provider)
    return LLMSettings("none", "", "", "")


def llm_available() -> bool:
    s = settings()
    if s.provider == "none" or not s.api_key:
        return False
    return s.provider != "openai_compatible" or bool(s.base_url and s.model)


def provider_label() -> str:
    s = settings()
    return f"{s.provider} ({_models(s)[0]})" if llm_available() else "off"


def _models(s: LLMSettings) -> list[str]:
    return [m.strip() for m in s.model.split(",") if m.strip()]


def extract_json(system: str, prompt: str, schema: dict[str, Any], pdf_bytes: bytes | None = None,
                 effort: str = "medium", max_tokens: int = 16000) -> dict[str, Any]:
    """Ask the configured LLM for JSON matching ``schema``. Raises LLMUnavailable on any failure."""
    if not llm_available():
        raise LLMUnavailable("no AI provider is configured")
    s = settings()
    if s.provider == "anthropic":
        data = _anthropic(s, system, prompt, schema, pdf_bytes, effort, max_tokens)
    else:
        # PDFs are sent as their text layer (callers always include it in the prompt).
        data = _openai_compatible(s, system, prompt, schema, max_tokens)
    missing = [k for k in schema.get("required", []) if k not in data]
    if missing:
        raise LLMUnavailable(f"the model's answer is missing {', '.join(missing)}")
    return data


# ----------------------------- Anthropic (Claude) -----------------------------
def _anthropic(s: LLMSettings, system: str, prompt: str, schema: dict[str, Any], pdf_bytes: bytes | None,
               effort: str, max_tokens: int) -> dict[str, Any]:
    import anthropic  # imported lazily so other providers don't need it configured

    content: list[dict[str, Any]] = []
    if pdf_bytes:
        content.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(pdf_bytes).decode()},
        })
    content.append({"type": "text", "text": prompt})
    try:
        response = anthropic.Anthropic(api_key=s.api_key, max_retries=2, timeout=120).beta.messages.create(
            model=s.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError as exc:
        raise LLMUnavailable(f"could not reach the Claude API: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LLMUnavailable("Claude API rate limit reached, try again shortly") from exc
    except anthropic.APIStatusError as exc:
        log.warning("Claude API error %s: %s", exc.status_code, exc.message)
        raise LLMUnavailable(f"Claude API error {exc.status_code}") from exc

    if response.stop_reason == "refusal":
        raise LLMUnavailable("the model declined to read this text")
    if response.stop_reason == "max_tokens":
        raise LLMUnavailable("the model response was cut off")
    text = next((b.text for b in response.content if getattr(b, "type", "") == "text"), "")
    return _parse_json(text)


# ----------------------------- OpenAI-compatible (Gemini, Groq, ...) ----------
def _http_post(url: str, headers: dict[str, str], payload: dict[str, Any]) -> httpx.Response:
    # A healthy model answers a policy read in well under a minute; a queued,
    # overloaded one can hang, so give up and fail over to the next model.
    with httpx.Client(timeout=httpx.Timeout(90, connect=10)) as client:
        return client.post(url, headers=headers, json=payload)


class _TryNextModel(Exception):
    """This model cannot answer now (retired, overloaded, timed out); try the next one."""


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
        body = body[0] if isinstance(body, list) else body
        return str(body.get("error", {}).get("message", ""))[:200]
    except (ValueError, AttributeError, IndexError):
        return response.text[:200]


def _openai_compatible(s: LLMSettings, system: str, prompt: str, schema: dict[str, Any],
                       max_tokens: int) -> dict[str, Any]:
    """Try each configured model in turn."""
    problems: list[str] = []
    for model in _models(s):
        try:
            return _ask_model(s, model, system, prompt, schema, max_tokens)
        except _TryNextModel as exc:
            log.warning("%s model %s unavailable: %s", s.provider, model, exc)
            problems.append(f"{model}: {exc}")
    raise LLMUnavailable(f"no {s.provider} model is available right now ({'; '.join(problems)})")


def _ask_model(s: LLMSettings, model: str, system: str, prompt: str, schema: dict[str, Any],
               max_tokens: int) -> dict[str, Any]:
    url = s.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {s.api_key}", "Content-Type": "application/json"}
    instructions = (
        f"{system}\n\nReply with ONLY a JSON object (no prose, no code fences) that matches this JSON Schema. "
        f"Use null where a value is unknown.\n{json.dumps(schema)}"
    )
    base = {
        "model": model,
        "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    # Prefer schema-constrained output; some providers reject parts of JSON Schema
    # (e.g. nullable type lists), so fall back to plain JSON mode, then to no constraint.
    formats = [
        {"response_format": {"type": "json_schema", "json_schema": {"name": "result", "schema": schema}}},
        {"response_format": {"type": "json_object"}},
        {},
    ]
    last_error = ""
    for extra in formats:
        response = _post_with_retry(s, url, headers, {**base, **extra})
        status = response.status_code
        if status == 429:
            raise LLMUnavailable(f"{s.provider} rate limit reached (free tiers allow a few requests per minute); "
                                 "try again shortly")
        if status in (401, 403):
            raise LLMUnavailable(f"{s.provider} rejected the API key")
        if status in (404, 500, 503):
            raise _TryNextModel(f"HTTP {status} {_error_text(response)}")
        if status == 400:
            last_error = _error_text(response)
            continue  # try a looser output format
        if status >= 300:
            raise LLMUnavailable(f"{s.provider} API error {status}: {_error_text(response)}")
        try:
            choice = response.json()["choices"][0]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable(f"unexpected response from {s.provider}") from exc
        if choice.get("finish_reason") == "length":
            raise LLMUnavailable("the model response was cut off")
        return _parse_json(choice.get("message", {}).get("content") or "")
    raise _TryNextModel(f"rejected every request format (HTTP 400: {last_error})")


def _post_with_retry(s: LLMSettings, url: str, headers: dict[str, str], payload: dict[str, Any]) -> httpx.Response:
    """Retry briefly on 500/503 - 'high demand' spikes are usually short."""
    for attempt in range(OVERLOAD_RETRIES + 1):
        try:
            response = _http_post(url, headers, payload)
        except httpx.TimeoutException as exc:
            raise _TryNextModel("timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailable(f"could not reach the {s.provider} API: {exc}") from exc
        if response.status_code not in (500, 503) or attempt == OVERLOAD_RETRIES:
            return response
        time.sleep(2 * (attempt + 1))
    return response  # unreachable; keeps type checkers happy


def _parse_json(text: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating code fences or a sentence around it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{") and "{" in text:
        text = text[text.index("{"): text.rindex("}") + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable("the model did not return valid JSON") from exc
    if not isinstance(data, dict):
        raise LLMUnavailable("the model did not return a JSON object")
    return data
