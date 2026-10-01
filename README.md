# Agentic Customer Feedback System

Turns free-text customer feedback into a structured, grounded triage report for a CS officer:
**intake → classification → tool-calling context agent → report → grounding checks → human review**.

Python 3.9+, LangChain core for tools, and the **OpenAI Python SDK** for model calls
(`openai.OpenAI` or `openai.AzureOpenAI`).
No other infrastructure: the data sources are mocked as JSON files in `data/`. Intake is through the **web UI**
(backed by FastAPI).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then fill in OpenAI *or* Azure OpenAI settings
```

| Env var | Required | Default |
|---|---|---|
| `OPENAI_API_KEY` | yes *(or Azure pair below)* | - |
| `OPENAI_MODEL` | no | `gpt-4o-mini` |
| `OPENAI_BASE_URL` | no (OpenAI-compatible proxies) | OpenAI default |
| `AZURE_OPENAI_API_KEY` | yes with endpoint *(or `OPENAI_API_KEY`)* | - |
| `AZURE_OPENAI_ENDPOINT` | yes with Azure key (e.g. `https://YOUR_RESOURCE.openai.azure.com/`) | - |
| `AZURE_OPENAI_API_VERSION` | no | `2024-08-01-preview` |
| `AZURE_OPENAI_DEPLOYMENT` | no (Azure deployment name) | falls back to `OPENAI_MODEL` / `gpt-4o-mini` |

If both Azure and OpenAI credentials are set, **Azure is preferred**. Without any key, every LLM stage falls back to deterministic degraded behaviour (same as `--simulate llm` / UI "All LLM stages").

## Run the UI

```bash
./scripts/start.sh
# open http://127.0.0.1:8000
```

Runs in the foreground so logs stay in that terminal. Stop with **Ctrl+C** or by closing/killing that terminal session.

Optional: `HOST=0.0.0.0 PORT=8080 ./scripts/start.sh`

(Equivalent: `.venv/bin/uvicorn feedback_agent.app.server:app --host 127.0.0.1 --port 8000`)

The UI lets you:
- pick a built-in sample from `samples/inputs/`
- upload a JSON file (same schema)
- type feedback manually
- optionally inject a failure (`simulate`: classifier / agent / reporter / llm / customer_db)

Results show the structured report plus the agent step trace. Each run is also saved under
`runs/<feedback_id>/` as `trace.jsonl`, `report.json`, and `report.md`.

### API (same backend the UI calls)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | API key / model status |
| `GET` | `/api/samples` | List sample inputs |
| `POST` | `/api/run` | Run from JSON body |
| `POST` | `/api/run/sample` | Run a named sample (`{"name": "01_happy_duplicate_charge"}`) |
| `POST` | `/api/run/upload` | Upload a `.json` file (`multipart/form-data`) |
| `GET` | `/api/runs/{feedback_id}` | Fetch a saved report + trace |

Interactive docs: `http://127.0.0.1:8000/docs`

### Offline tests & sample regeneration

```bash
.venv/bin/python -m pytest -q
./scripts/run_samples.sh      # writes samples/outputs/*.md and *.trace.jsonl via the shared service
```

## Architecture

```
FeedbackSubmission (text, customer_id/email, channel, timestamp)
        │
        ▼
┌──────────────────┐  1 structured-output LLM call, no tools
│ 1. Classifier    │  → category, secondary_category, urgency, sentiment, confidence, rationale
└──────────────────┘  ambiguous = confidence < 0.6 or secondary_category set
        │
        ▼
┌──────────────────────────────────────────────┐
│ 2. Context agent (tool-calling loop, ≤8 turns)│  LLM decides which tools to call and with what args;
│    lookup_customer      → data/customers.json │  results go back as ToolMessages until it stops.
│    get_order_history    → data/customers.json │  Coverage check: if it stops without consulting
│    get_cs_guideline     → data/guidelines.json│  customer / guideline / policy, it is reminded once.
│    search_company_policy→ data/policies.json  │
└──────────────────────────────────────────────┘
        │  GatheredContext (customer, orders, SOPs, policies, tool errors, agent findings)
        ▼
┌──────────────────┐  structured-output LLM call over ONLY the gathered context
│ 3. Reporter      │  → summary, customer context, references, actions (+basis, approval), confidence
└──────────────────┘
        │
        ▼
┌──────────────────────────┐  deterministic, no LLM
│ 4. Grounding + flagging  │  drop citations to sources never retrieved; pin customer context to lookup result;
└──────────────────────────┘  add flags; cap confidence; set needs_human_review
        │
        ▼
FeedbackReport (review.status = pending_review) ──► CS officer: approve / override / reject ──► dispatch (stub)
```

| Module | Responsibility |
|---|---|
| `feedback_agent/config.py`, `models.py` | Settings and shared schemas |
| `feedback_agent/azure_llm.py` | OpenAI / Azure OpenAI client + structured-output / tool-calling adapter |
| `feedback_agent/agent/` | Pipeline, classifier, context agent, tools, reporter, prompts |
| `feedback_agent/store/` | Mock CRM / policy / guideline data access |
| `feedback_agent/app/` | FastAPI server, shared run service, render, human-review stub |
| `feedback_agent/observability/` | Step trace to stderr and `trace.jsonl` |
| `data/` | Mock CRM (5 customers), 8 policies, 8 SOPs |
| `web/` | Static UI served by FastAPI |

### Error handling

| Situation | Behaviour | Visible as |
|---|---|---|
| Ambiguous classification | Pipeline continues; agent also fetches the secondary category's SOP; reporter must add a "confirm intent / manual triage" action; confidence capped at low | `AMBIGUOUS_CLASSIFICATION` |
| No matching customer | Tool returns `{"found": false}` (not an exception); customer context is overwritten with a deterministic "not found, verify identity" line so it cannot be hallucinated | `CUSTOMER_NOT_FOUND` / `NO_CUSTOMER_IDENTIFIER` |
| No matching policy | Report has no references; confidence capped at medium | `NO_POLICY_FOUND` |
| Tool raises | Error is returned to the LLM as the tool result (it may retry once), and recorded | `TOOL_ERROR` |
| Model cites a source it never retrieved | Reference / action basis removed | `UNGROUNDED_REFERENCE_REMOVED` |
| Classifier LLM fails | Fallback: `other` / medium / confidence 0 | `CLASSIFIER_FAILED` |
| Agent LLM fails | Fixed retrieval plan by category runs the same tools | `AGENT_FALLBACK` |
| Reporter LLM fails | Deterministic report from retrieved data + "triage manually" action | `REPORT_GENERATION_FAILED` |
| Agent loops too long | Stops at 8 turns and reports with what it has | `AGENT_STEP_LIMIT` |

Transient API errors (429/5xx/timeouts) are retried twice by the OpenAI client before a stage is treated as failed.

### Human in the loop

The agent never takes action. Every report is saved with `review.status = pending_review`; `needs_human_review`
additionally marks reports the officer should not rubber-stamp (confidence below high, critical/abuse cases, or an
action whose policy requires extra approval such as refunds above 500 USD). `feedback_agent/app/review.py` is a stub
for approve / override / reject (with audit log); `dispatch_actions()` only runs after approval. In production this
would be a queue in the ticketing tool; the audit log doubles as labelled eval data.

## Sample runs

Inputs are in `samples/inputs/`, outputs (report + trace) in `samples/outputs/`:

| # | Scenario | Exercises |
|---|---|---|
| 01 | Pro customer double-charged | Happy path: billing → order history → refund policy |
| 02 | Enterprise export outage, identified by email only | Critical urgency, escalation + SLA + credits |
| 03 | "Things aren't working… not worth what we pay" | Ambiguous category (churn vs bug) |
| 04 | Insulting message from an unknown email | Abuse handling + missing customer record |
| 05 | Praise + feature request, customer DB down | Tool failure (`simulate: customer_db`) |
| 06 | Sample 01 with all LLM calls failing | Full degradation (`simulate: llm`) |

## Design write-up

See [DESIGN.md](DESIGN.md).

## AI assistance

_TODO: describe how you used AI assistance (e.g. Cursor for scaffolding/boilerplate, which decisions were yours)._
