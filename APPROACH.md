# APPROACH.md

## How I worked

1. Read the brief, policy v5.0 and the worked examples, and listed every rule, number and ordering detail.
2. Profiled the data: free-text lines, codes vs descriptions, the trap rows (qty 0, plant `PUN2`, the
   duplicate servo drive), blocked or expired contracts, PDF-only contracts, multi-line PRs.
3. Built the **engine first, as a pure library** (`backend/app/engine`) and ran it against all 14 worked
   examples until every route, supplier, price, value, clubbing and grouping matched. Then I designed and
   checked the supplier ranking against the 3 published lists.
4. Turned the hard-coded numbers into the **policy JSON schema**, so v5.0 is data and the engine only
   interprets it. Added the amendment operations, the plain-language diff and the versioning.
5. Built the API, persistence, chat intake and the React UI around the engine.
6. Wrote DECISIONS.md as I went, every time the policy did not say what to do.

## Architecture

```
frontend/ (React + Vite + TS)  ── /api ──>  backend/app/api        FastAPI routes (thin)
                                            backend/app/services   persistence, runs, approvals, chat,
                                                                   policy drafts, contract import, LLM
                                            backend/app/engine     pure decision engine, no DB / no HTTP
                                              policy.py   policy schema + condition language + versions
                                              matcher.py  item matching (1.1/1.2)
                                              pipeline.py validate → match → club → value → rules → consolidate
                                              contracts.py, suppliers.py, amend.py, describe.py
                                            SQLite (local) / Postgres (compose, hosted) via SQLAlchemy
```

- **Engine and UI are separate.** The engine takes master data + a `PolicyBook` and returns results. It
  is tested without a database. The same engine runs batch uploads, `POST /api/process-batch`, chat
  previews and re-processing after an amendment.
- **Policy as data:** parameters, conditional overrides and an ordered rule table whose conditions test
  named requirement *facts*. An amendment becomes a list of validated operations, which produces a new
  version. That version is diffed in plain language and activated from its effective date on approval.
- **Decisions are stored per run**, so run 1 (v5.0) and run 2 (after the amendment) sit side by side, and
  each row records the policy version and clause used.

## AI tools used

- **For building:** Claude Code (Claude Opus 5.5) as a pair programmer, for scaffolding, writing code and
  tests, and drafting the docs. I reviewed the decisions, especially the policy interpretation and the
  ranking weights, against the worked examples.
- **Inside the app (optional).** The provider is chosen by environment variable: Claude (`claude-opus-5-5`,
  paid), Google Gemini (free tier; `gemini-flash-lite-latest` with fallback to larger Flash models), or any OpenAI-compatible API.
  All of them sit behind one function (`services/llm.py: extract_json`), which returns validated JSON.
  - *Policy reader:* turns an uploaded amendment or a chat description into change operations, returned
    as structured JSON, validated, and sent back once for correction if invalid. It never activates
    anything; a person reviews the plain-language diff and approves.
  - *Chat understanding:* extracts quantity, plant, date and urgency from free text. Item identification
    is always the deterministic matcher, so the model cannot invent items.
  - *Contract PDF fallback:* used only when a PDF does not follow Corvo's contract template.
- With no AI key configured the app still works fully. Chat uses the rule-based parser. Policy changes go
  through the structured editor or the JSON editor, which run through the same draft → review → approve flow.

## Testing

`backend/tests` has 59 tests:
- every worked example and the 3 preferred lists
- blocked, inactive and expired suppliers and contracts, the ceiling order, the emergency limit, the 24h
  consolidation boundary, NOT_PROCESSED cases
- amendments: a raised limit, a plant-only rule, a plant-only parameter override, effective-date switching
  within a batch, invalid amendments rejected
- the API end to end: batch JSON/CSV, the policy lifecycle and reprocessing, approve/override/audit, the
  preferred-list export, contract PDF extraction, chat flows (one-shot, vague item, unknown item, follow-ups)
- the AI layer with HTTP faked: provider selection, the Gemini request, format fallback, rate-limit / bad-key /
  bad-JSON handling, fallback across retired or overloaded models, and a described policy change turned into a draft end to end

Run them with `cd backend && python -m pytest`.
