# Corvo PR-to-PO

A web app where employees raise purchase requests by chatting, and the system decides, by following
Corvo's procurement policy, whether to place a purchase order or raise a sourcing request. The policy is
stored as versioned data: Procurement uploads or describes an amendment, reviews what will change in plain
language, and approves it from an effective date. No code change is needed.

- **Hosted app:** _add the Render URL here after deploying (see below)_
- Design notes: [DECISIONS.md](DECISIONS.md) (judgment calls, supplier ranking) · [APPROACH.md](APPROACH.md) (architecture, AI use, tests)

## Run it

### One command (Docker)

```bash
docker compose up --build
# open http://localhost:8000
```

Optional AI (the policy reader that reads an uploaded amendment or a change described in words, plus better
chat understanding). Everything else works without it. Set **one** key before starting:

| Provider | Cost | Variable |
|---|---|---|
| Google Gemini | free tier ([get a key](https://aistudio.google.com/apikey)) | `GEMINI_API_KEY=...` (tries `gemini-flash-lite-latest`, then larger Flash models if busy; override with `LLM_MODEL`) |
| Anthropic Claude | paid | `ANTHROPIC_API_KEY=sk-ant-...` (model `claude-opus-5-5`) |
| Any OpenAI-compatible API (Groq, OpenRouter...) | varies | `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |

e.g. `GEMINI_API_KEY=... docker compose up --build`, or put the line in a `.env` file next to
`docker-compose.yml`. Check `/api/health`: it shows `"ai": true` and which provider is in use.

### Without Docker

```bash
# backend (Python 3.11+)
cd backend
python -m venv .venv && .venv/Scripts/activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
# frontend
cd ../frontend && npm ci && npm run build && cd ../backend
uvicorn app.main:app --port 8000                      # serves the API and the built UI on :8000
```

For UI development run `npm run dev` in `frontend/` (port 5173, proxies `/api` to :8000).

On first start the database is seeded from the kit: item, supplier, PO history and plant masters, the
contract register, the PDF-only contracts (extracted from `contracts/pdf`), and policy v5.0.
SQLite is used unless `DATABASE_URL` is set (compose and Render use Postgres).

### Tests

```bash
cd backend && python -m pytest        # 59 tests, incl. all worked examples
```

## Deploy (Render, free)

1. Push this repo to GitHub (public).
2. In Render: **New → Blueprint**, pick the repo. `render.yaml` creates the Docker web service and a free Postgres.
3. Optionally set `GEMINI_API_KEY` (free) or `ANTHROPIC_API_KEY` on the web service (Environment tab).
4. Put the service URL at the top of this README.

## Using the app

| Screen | Who | One main action |
|---|---|---|
| **Raise a request** | Requester | Chat: "need 200 nitrile gloves for Chakan by next Friday". The assistant matches the item, asks for anything missing, confirms and creates the PR. |
| **Buyer workspace** | Buyer | Upload a PR batch CSV (or process chat PRs). Each requirement shows its lines, value, decision, supplier and price or suggested suppliers, and *why* (clause, policy version, contract or PO relied on). **Approve** creates the PO / RFQ / RFP; **Override** needs a written reason. Export the decisions CSV. |
| **Policy** | Procurement | **Propose a change**: upload an amendment (.pdf/.docx/.txt), describe it in words, or use the structured editor. Review the plain-language diff, set the effective date, **Approve**. |
| **Preferred suppliers** | Buyer | Rankings per item as of any date, with every score component. **Export top 3 as CSV.** |
| **Contracts** | Buyer | **Add from signed PDF** (extracted, reviewed, saved), add manually, or reload the register CSVs. |
| **Audit log** | Anyone | Every run, approval, override and policy change, with who and when; created POs and sourcing requests. |

Use the **Acting as** picker (bottom left) to switch between requester, buyer and procurement users.

### Blind-test runbook

1. **Run 1:** Workspace → *Upload PR batch CSV* → *Export decisions CSV*.
2. **Run 2:** Policy → *Propose a change* → upload the amendment → check the changes and effective date →
   *Approve*. Workspace → open the run-1 batch → *Reprocess under current policy* → *Export decisions CSV*.
3. Preferred suppliers → set the date → *Export top 3 as CSV*.

## API

| Method & path | Purpose |
|---|---|
| `POST /api/process-batch` | PR batch CSV as multipart `file` or raw body. Returns JSON decisions; `?format=csv` returns the export CSV. The run is stored. |
| `GET /api/runs/{id}/export.csv` | Decisions in the output format (`?include_overrides=true` for buyer-final). |
| `POST /api/runs/{id}/reprocess` | Same input, re-decided under the currently active policy versions. |
| `POST /api/runs/{id}/requirements/{group}/approve` · `/override` | Buyer actions (override requires `comment`). |
| `GET /api/preferred-suppliers/export.csv?as_of=YYYY-MM-DD&top=3` | Preferred supplier lists. |
| `GET /api/policies` · `POST /api/policies/drafts/{upload,chat,operations,document}` · `POST /api/policies/{id}/approve` | Policy versions and amendments. |
| `POST /api/contracts/extract` · `POST /api/contracts` · `POST /api/contracts/register` | Contracts. |
| `POST /api/chat` | Chat intake turn. |

Interactive docs at `/docs`.

```bash
curl -F "file=@pr_batches/sample_pr_batch.csv" "http://localhost:8000/api/process-batch?format=csv" -o decisions.csv
```

Output columns: `pr_no, pr_line, resolved_item_code, group_id, po_id, decision, route, supplier_id,
unit_price, group_value_inr, suggested_supplier_ids, policy_version, reason`.

## Repository layout

```
backend/app/engine     pure decision engine (policy schema, matcher, clubbing, valuation, rules, ranking, consolidation)
backend/app/services   persistence, runs & approvals, chat intake, policy drafts, contract import, LLM wrapper
backend/app/api        FastAPI routes
backend/app/policies   policy_v5.0.json - the baseline policy as data
backend/tests          worked examples, rules, amendments, API
frontend/src           React UI
master_data/ contracts/ pr_batches/   the task kit (seed data)
```
