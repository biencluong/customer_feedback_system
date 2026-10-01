# Design Write-up

## Why this structure

**A fixed three-stage pipeline around one agentic step.** Classification and report writing do not need autonomy:
each is a single structured-output call with a known schema. Context gathering is where the right actions depend on
the case (billing needs order history, a critical bug needs escalation and SLA policies, an ambiguous message needs
two SOPs), so only that step is a ReAct-style tool-calling loop. This keeps the agent's freedom where it adds value
and keeps cost, latency, and failure surface predictable everywhere else.

**Classification as its own step, not a tool.** A separate call gives a typed, auditable classification (with a
confidence and rationale) that downstream steps and the flags can depend on, and it can be evaluated and swapped
(e.g. for a cheaper model or a fine-tuned classifier) on its own. The cost is one extra LLM call; letting the agent
classify as part of the loop would save that call but make the category implicit and harder to test.

**Single agent, not multi-agent.** Four read-only tools over small data don't justify coordination overhead.
A multi-agent split (e.g. a separate policy researcher) would add latency and new failure modes without better output.

**Custom loop on LangChain primitives** (`bind_tools`, `with_structured_output`) instead of a prebuilt executor, so
every decision and tool result is explicit in the trace, and a step cap and a coverage check are easy to add.
The coverage check reminds the agent once if it stops without consulting the customer, the SOP, or a policy: it
keeps the agent's autonomy over *how* to search while guaranteeing the minimum grounding the report needs.

**Grounding is enforced in code, not only in the prompt.** After the reporter runs, citations to sources that were
never retrieved are removed and flagged, customer context is overwritten when no record was found, and confidence is
capped by deterministic flags, so the model cannot claim "high" confidence on an ambiguous case or unknown customer.

## Making it production-ready

- **Reliability:** idempotent processing keyed by feedback ID behind a queue; per-tool timeouts and circuit breakers;
  schema-validated tool outputs; a model fallback (e.g. a second provider) instead of the current deterministic one.
- **Evaluation:** a labelled set (seeded from the review audit log, where every override is a correction) scored on
  category/urgency accuracy, citation precision (cited IDs vs. an expected set), and action correctness; run in CI on
  every prompt or model change. LangSmith (already supported via env vars) for online traces.
- **Retrieval:** real CRM/ticketing APIs; hybrid (BM25 + embeddings) search once policies grow beyond a handful of
  documents; versioned policies so a report records which version it relied on.
- **Cost/latency:** a small model for classification, a larger one only for the report; skip the agent loop for
  high-confidence praise; parallel tool calls are already supported; cache customer lookups.
- **Safety:** stronger prompt-injection defences (input screening, a separate check that suggested actions match
  policy), PII redaction in logs, role-based access to the review step.

## Deliberately left out

- **Bonus items** (clarifying questions to the customer, batch mode, eval harness, injection guardrails beyond basic
  delimiting): chose depth on the required flow and failure handling over breadth.
- **Vector store:** 8 policies are better served by keyword search that is easy to inspect; embeddings add
  infrastructure without improving results at this size.
- **A polished CS officer review UI:** reports land in `pending_review` with a code stub for approve/override/reject;
  the web UI focuses on intake and inspecting the agent report/trace.
- **Real action execution:** refunds, escalations, and tickets are only suggested; `dispatch_actions()` is a stub
  gated on human approval.
