"""Report generation (LLM, structured output) followed by deterministic grounding checks and flagging."""

from __future__ import annotations

from typing import List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from .classifier import is_ambiguous
from .context_agent import GatheredContext
from ..models import (
    Classification,
    FeedbackReport,
    FeedbackSubmission,
    Flag,
    Reference,
    ReportDraft,
    SuggestedAction,
)
from .prompts import REPORTER_SYSTEM, render_feedback

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
# Flags that cap the report's overall confidence, regardless of what the model claims.
_CAP_LOW = {"CLASSIFIER_FAILED", "AMBIGUOUS_CLASSIFICATION", "REPORT_GENERATION_FAILED"}
_CAP_MEDIUM = {
    "CUSTOMER_NOT_FOUND",
    "NO_CUSTOMER_IDENTIFIER",
    "TOOL_ERROR",
    "AGENT_FALLBACK",
    "AGENT_STEP_LIMIT",
    "NO_POLICY_FOUND",
    "UNGROUNDED_REFERENCE_REMOVED",
}


def generate_draft(
    llm, submission: FeedbackSubmission, classification: Classification, ambiguous: bool, ctx: GatheredContext
) -> ReportDraft:
    prompt = (
        f"{render_feedback(submission)}\n\n"
        f"CLASSIFICATION: {classification.model_dump_json()}\n"
        f"AMBIGUOUS: {ambiguous}\n\n"
        f"AVAILABLE_SOURCE_IDS: {ctx.source_ids()}\n\n"
        f"CONTEXT:\n{ctx.to_prompt_json()}"
    )
    structured = llm.with_structured_output(ReportDraft, method="function_calling")
    return structured.invoke([SystemMessage(REPORTER_SYSTEM), HumanMessage(prompt)])


def describe_customer(ctx: GatheredContext, submission: FeedbackSubmission) -> str:
    c = ctx.customer
    if c is None:
        ids = f"customer_id={submission.customer_id!r}, email={submission.customer_email!r}"
        return f"No customer record found ({ids}). Verify the customer's identity before acting."
    open_tickets = [t["ticket_id"] for t in c["past_tickets"] if t["status"] == "open"]
    return (
        f"{c['name']} ({c['customer_id']}), {c['tier']} tier, customer for {c['tenure_months']} months, "
        f"health score {c['health_score']}, open tickets: {', '.join(open_tickets) or 'none'}."
    )


def degraded_draft(submission: FeedbackSubmission, ctx: GatheredContext) -> ReportDraft:
    """Deterministic report used when the report LLM call fails. Contains only retrieved facts."""
    excerpt = submission.text if len(submission.text) <= 280 else submission.text[:280] + "…"
    return ReportDraft(
        summary=f"[Automatic summary unavailable - verbatim excerpt] {excerpt}",
        customer_context=describe_customer(ctx, submission),
        references=[
            Reference(source_id=sid, title=doc["title"], relevance="Retrieved for the assigned category; verify fit.")
            for sid, doc in {**ctx.guidelines, **ctx.policies}.items()
        ],
        suggested_actions=[
            SuggestedAction(
                action="Read and triage this feedback manually; automated analysis was unavailable.",
                owner="CS officer",
                basis=None,
                requires_approval=False,
            )
        ],
        confidence="low",
        uncertainty_notes=["Report produced without LLM analysis."],
    )


def context_flags(
    submission: FeedbackSubmission,
    classification: Classification,
    ctx: GatheredContext,
    threshold: float,
    classifier_failed: bool,
) -> List[Flag]:
    flags: List[Flag] = []
    if not classifier_failed and is_ambiguous(classification, threshold):
        alt = f", could also be {classification.secondary_category}" if classification.secondary_category else ""
        flags.append(
            Flag(
                code="AMBIGUOUS_CLASSIFICATION",
                detail=f"{classification.category} at confidence {classification.confidence:.2f}{alt}.",
            )
        )
    if not (submission.customer_id or submission.customer_email):
        flags.append(Flag(code="NO_CUSTOMER_IDENTIFIER", detail="Submission has no customer ID or email."))
    elif ctx.customer is None:
        flags.append(Flag(code="CUSTOMER_NOT_FOUND", detail=describe_customer(ctx, submission)))
    for err in ctx.tool_errors:
        flags.append(Flag(code="TOOL_ERROR", detail=err))
    if ctx.mode == "fallback":
        flags.append(Flag(code="AGENT_FALLBACK", detail="Context gathered by fixed fallback plan, not the agent."))
    if ctx.hit_step_limit:
        flags.append(Flag(code="AGENT_STEP_LIMIT", detail="Agent hit its step limit; context may be incomplete."))
    if not ctx.policies and classification.category != "praise":
        flags.append(Flag(code="NO_POLICY_FOUND", detail="No company policy was retrieved for this case."))
    if classification.urgency == "critical" or classification.category == "abuse":
        flags.append(Flag(code="HIGH_RISK", detail=f"{classification.category} / {classification.urgency} urgency."))
    return flags


def enforce_grounding(draft: ReportDraft, ctx: GatheredContext, submission: FeedbackSubmission) -> List[Flag]:
    """Drop citations to sources the agent never retrieved, and pin customer facts to the lookup result."""
    flags: List[Flag] = []
    retrieved = set(ctx.source_ids())

    kept = []
    for ref in draft.references:
        if ref.source_id in retrieved:
            kept.append(ref)
        else:
            flags.append(Flag(code="UNGROUNDED_REFERENCE_REMOVED", detail=f"{ref.source_id} was never retrieved."))
    draft.references = kept

    for action in draft.suggested_actions:
        if action.basis and action.basis not in retrieved:
            flags.append(
                Flag(code="UNGROUNDED_REFERENCE_REMOVED", detail=f"Action basis {action.basis} was never retrieved.")
            )
            action.basis = None

    if ctx.customer is None:
        draft.customer_context = describe_customer(ctx, submission)
    return flags


def finalize_report(
    submission: FeedbackSubmission,
    classification: Classification,
    ctx: GatheredContext,
    draft: Optional[ReportDraft],
    stage_flags: List[Flag],
    threshold: float,
) -> FeedbackReport:
    classifier_failed = any(f.code == "CLASSIFIER_FAILED" for f in stage_flags)
    flags = list(stage_flags) + context_flags(submission, classification, ctx, threshold, classifier_failed)
    if draft is None:
        draft = degraded_draft(submission, ctx)
    flags += enforce_grounding(draft, ctx, submission)
    flags += [Flag(code="MODEL_UNCERTAINTY", detail=note) for note in draft.uncertainty_notes]

    codes = {f.code for f in flags}
    confidence = draft.confidence
    if codes & _CAP_LOW:
        confidence = "low"
    elif codes & _CAP_MEDIUM and _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK["medium"]:
        confidence = "medium"

    needs_review = (
        confidence != "high"
        or "HIGH_RISK" in codes
        or any(a.requires_approval for a in draft.suggested_actions)
    )

    return FeedbackReport(
        feedback=submission,
        classification=classification,
        summary=draft.summary,
        customer_found=ctx.customer is not None,
        customer_context=draft.customer_context,
        references=draft.references,
        suggested_actions=draft.suggested_actions,
        confidence=confidence,
        needs_human_review=needs_review,
        flags=flags,
        sources_consulted=ctx.source_ids(),
        degraded=bool(codes & {"CLASSIFIER_FAILED", "AGENT_FALLBACK", "REPORT_GENERATION_FAILED"}),
    )
