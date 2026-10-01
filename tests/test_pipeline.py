"""Offline tests: a scripted fake LLM drives the real pipeline, tools, and grounding checks (no API key needed)."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage

from feedback_agent.agent.context_agent import GatheredContext
from feedback_agent.store.data_store import DataStore
from feedback_agent.models import Classification, FeedbackSubmission, Reference, ReportDraft, SuggestedAction
from feedback_agent.agent.pipeline import run_pipeline
from feedback_agent.agent.reporter import enforce_grounding
from feedback_agent.app.review import apply_review, dispatch_actions
from feedback_agent.agent.tools import build_tools
from feedback_agent.observability.tracing import Tracer


class FakeLLM:
    """Returns scripted tool-calling turns and structured outputs in order."""

    def __init__(self, agent_turns, structured):
        self.agent_turns = list(agent_turns)
        self.structured = list(structured)

    def bind_tools(self, tools):
        return self

    def with_structured_output(self, schema, **kwargs):
        outer = self

        class _Runner:
            def invoke(self, messages):
                return outer.structured.pop(0)

        return _Runner()

    def invoke(self, messages):
        return self.agent_turns.pop(0)


def _call(name, args, i):
    return {"name": name, "args": args, "id": f"call_{i}", "type": "tool_call"}


BILLING = Classification(
    category="billing_issue", sentiment="negative", urgency="high", confidence=0.95, rationale="charged twice"
)


def _billing_draft(extra_ref=None):
    refs = [Reference(source_id="POL-REFUND-001", title="Refund Policy", relevance="duplicate charges refunded")]
    if extra_ref:
        refs.append(Reference(source_id=extra_ref, title="Made up", relevance="hallucinated"))
    return ReportDraft(
        summary="Customer was charged twice.",
        customer_context="Bob Tran, pro tier.",
        references=refs,
        suggested_actions=[
            SuggestedAction(action="Refund ORD-9121", owner="CS officer", basis="POL-REFUND-001", requires_approval=False)
        ],
        confidence="high",
    )


@pytest.fixture
def submission():
    return FeedbackSubmission(feedback_id="FB-T1", text="Charged twice, please refund.", customer_id="C002")


def test_happy_path_agent_calls_tools_and_grounds_report(submission):
    llm = FakeLLM(
        agent_turns=[
            AIMessage(content="", tool_calls=[_call("lookup_customer", {"customer_id": "C002"}, 1)]),
            AIMessage(
                content="",
                tool_calls=[
                    _call("get_order_history", {"customer_id": "C002"}, 2),
                    _call("get_cs_guideline", {"category": "billing_issue"}, 3),
                    _call("search_company_policy", {"query": "duplicate charge refund"}, 4),
                ],
            ),
            AIMessage(content="- Duplicate ORD-9120/ORD-9121 per POL-REFUND-001"),
        ],
        structured=[BILLING, _billing_draft()],
    )
    tracer = Tracer(verbose=False)
    report = run_pipeline(submission, tracer=tracer, llm=llm)

    assert report.customer_found
    assert "POL-REFUND-001" in report.sources_consulted and "SOP-BILL-01" in report.sources_consulted
    assert report.confidence == "high" and not report.flags and not report.degraded
    assert [e["tool"] for e in tracer.events if e["kind"] == "tool.call"] == [
        "lookup_customer",
        "get_order_history",
        "get_cs_guideline",
        "search_company_policy",
    ]


def test_agent_is_reminded_when_it_skips_required_sources(submission):
    llm = FakeLLM(
        agent_turns=[
            AIMessage(content="done early"),
            AIMessage(
                content="",
                tool_calls=[
                    _call("lookup_customer", {"customer_id": "C002"}, 1),
                    _call("get_cs_guideline", {"category": "billing_issue"}, 2),
                    _call("search_company_policy", {"query": "refund"}, 3),
                ],
            ),
            AIMessage(content="findings"),
        ],
        structured=[BILLING, _billing_draft()],
    )
    tracer = Tracer(verbose=False)
    run_pipeline(submission, tracer=tracer, llm=llm)
    assert any(e["kind"] == "agent.coverage_reminder" for e in tracer.events)


def test_hallucinated_reference_is_removed_and_flagged(submission):
    ctx = GatheredContext()
    tools = {t.name: t for t in build_tools(DataStore())}
    ctx.record("search_company_policy", tools["search_company_policy"].invoke({"query": "refund duplicate"}))
    ctx.record("lookup_customer", tools["lookup_customer"].invoke({"customer_id": "C002"}))
    draft = _billing_draft(extra_ref="POL-FAKE-999")
    flags = enforce_grounding(draft, ctx, submission)
    assert [r.source_id for r in draft.references] == ["POL-REFUND-001"]
    assert flags[0].code == "UNGROUNDED_REFERENCE_REMOVED"


def test_unknown_customer_is_flagged_and_not_invented():
    sub = FeedbackSubmission(text="You people are useless", customer_email="nobody@unknown.example")
    abuse = Classification(category="abuse", sentiment="negative", urgency="high", confidence=0.9, rationale="insults")
    draft = _billing_draft()
    draft.customer_context = "Loyal enterprise customer since 2019."  # model hallucination
    llm = FakeLLM(
        agent_turns=[
            AIMessage(
                content="",
                tool_calls=[
                    _call("lookup_customer", {"email": "nobody@unknown.example"}, 1),
                    _call("get_cs_guideline", {"category": "abuse"}, 2),
                    _call("search_company_policy", {"query": "abuse conduct"}, 3),
                ],
            ),
            AIMessage(content="customer not found"),
        ],
        structured=[abuse, draft],
    )
    report = run_pipeline(sub, llm=llm)
    codes = {f.code for f in report.flags}
    assert not report.customer_found
    assert "No customer record found" in report.customer_context
    assert {"CUSTOMER_NOT_FOUND", "HIGH_RISK"} <= codes
    assert report.needs_human_review


def test_ambiguous_classification_caps_confidence(submission):
    vague = Classification(
        category="churn_risk",
        secondary_category="bug_report",
        sentiment="negative",
        urgency="medium",
        confidence=0.45,
        rationale="vague",
    )
    llm = FakeLLM(
        agent_turns=[
            AIMessage(
                content="",
                tool_calls=[
                    _call("lookup_customer", {"customer_id": "C002"}, 1),
                    _call("get_cs_guideline", {"category": "churn_risk"}, 2),
                    _call("search_company_policy", {"query": "retention churn"}, 3),
                ],
            ),
            AIMessage(content="findings"),
        ],
        structured=[vague, _billing_draft()],
    )
    report = run_pipeline(submission, llm=llm)
    assert report.confidence == "low"
    assert any(f.code == "AMBIGUOUS_CLASSIFICATION" for f in report.flags)


def test_full_llm_outage_produces_degraded_grounded_report(submission):
    report = run_pipeline(submission, simulate="llm")
    codes = {f.code for f in report.flags}
    assert report.degraded and report.confidence == "low"
    assert {"CLASSIFIER_FAILED", "AGENT_FALLBACK", "REPORT_GENERATION_FAILED"} <= codes
    assert report.customer_found  # deterministic fallback still retrieved the customer


def test_tool_failure_is_reported_to_agent_not_raised(submission):
    llm = FakeLLM(
        agent_turns=[
            AIMessage(content="", tool_calls=[_call("lookup_customer", {"customer_id": "C002"}, 1)]),
            AIMessage(
                content="",
                tool_calls=[
                    _call("get_cs_guideline", {"category": "billing_issue"}, 2),
                    _call("search_company_policy", {"query": "refund"}, 3),
                ],
            ),
            AIMessage(content="customer db down"),
        ],
        structured=[BILLING, _billing_draft()],
    )
    report = run_pipeline(submission, llm=llm, simulate="customer_db")
    assert any(f.code == "TOOL_ERROR" for f in report.flags)
    assert not report.customer_found and report.confidence == "medium"


def test_review_gates_action_dispatch(tmp_path, submission):
    report = run_pipeline(submission, simulate="llm")
    path = tmp_path / "FB-T1" / "report.json"
    path.parent.mkdir()
    path.write_text(report.model_dump_json())
    assert dispatch_actions(report) == []

    reviewed = apply_review(path, "override", "officer.kim", urgency="high", actions=["Refund ORD-9121"])
    assert reviewed.review.status == "overridden"
    assert dispatch_actions(reviewed) == ["[stub] would execute: Refund ORD-9121 (owner: CS officer)"]
    assert json.loads((tmp_path / "review_log.jsonl").read_text())["overrides"]["urgency"]["to"] == "high"
    with pytest.raises(ValueError):
        apply_review(path, "approve", "someone.else")
