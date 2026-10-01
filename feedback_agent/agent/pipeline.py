"""Orchestration: classify -> gather context (agent loop) -> write report -> grounding checks.

Each LLM stage has a fallback so a failure degrades the report (and flags it) instead of aborting the run.
"""

from __future__ import annotations

from typing import List, Optional

from .classifier import classify, fallback_classification, is_ambiguous
from ..config import Settings, build_llm
from .context_agent import fallback_gather, gather_context
from ..store.data_store import DataStore
from ..models import FeedbackReport, FeedbackSubmission, Flag, ReportDraft
from .reporter import finalize_report, generate_draft
from .tools import build_tools
from ..observability.tracing import Tracer

# "llm" fails every LLM stage; "customer_db" makes the lookup_customer tool raise.
SIMULATIONS = ("classifier", "agent", "reporter", "llm", "customer_db")


class SimulatedLLMError(RuntimeError):
    pass


def _short(e: Exception) -> str:
    msg = str(e).splitlines()[0] if str(e) else ""
    return f"{type(e).__name__}: {msg[:160]}"


def run_pipeline(
    submission: FeedbackSubmission,
    settings: Optional[Settings] = None,
    store: Optional[DataStore] = None,
    tracer: Optional[Tracer] = None,
    simulate: Optional[str] = None,
    llm=None,
) -> FeedbackReport:
    settings = settings or Settings()
    store = store or DataStore()
    tracer = tracer or Tracer(verbose=False)
    tools = build_tools(store, failing_tool="lookup_customer" if simulate == "customer_db" else None)
    stage_flags: List[Flag] = []

    tracer.event("intake", submission=submission.model_dump(mode="json"), model=settings.model, simulate=simulate)

    if llm is None:
        try:
            llm = build_llm(settings)
        except Exception as e:
            tracer.event("llm.unavailable", error=_short(e))

    def llm_for(stage: str):
        if simulate in (stage, "llm"):
            raise SimulatedLLMError(f"simulated {stage} LLM failure")
        if llm is None:
            raise RuntimeError("LLM client unavailable (are AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT set?)")
        return llm

    # 1. Classification
    try:
        with tracer.span("classify"):
            classification = classify(llm_for("classifier"), submission)
    except Exception as e:
        classification = fallback_classification(_short(e))
        stage_flags.append(Flag(code="CLASSIFIER_FAILED", detail=_short(e)))
    ambiguous = is_ambiguous(classification, settings.ambiguity_threshold)
    tracer.event("classification", **classification.model_dump(), ambiguous=ambiguous)

    # 2. Context gathering
    try:
        with tracer.span("gather_context"):
            ctx = gather_context(
                llm_for("agent"), tools, submission, classification, ambiguous, tracer, settings.max_agent_steps
            )
    except Exception as e:
        tracer.event("agent.fallback", reason=_short(e))
        ctx = fallback_gather(tools, submission, classification, tracer)
    tracer.event(
        "context.summary",
        mode=ctx.mode,
        customer_found=ctx.customer is not None,
        sources=ctx.source_ids(),
        tool_errors=ctx.tool_errors,
    )

    # 3. Report
    draft: Optional[ReportDraft] = None
    try:
        with tracer.span("generate_report"):
            draft = generate_draft(llm_for("reporter"), submission, classification, ambiguous, ctx)
    except Exception as e:
        stage_flags.append(Flag(code="REPORT_GENERATION_FAILED", detail=_short(e)))

    report = finalize_report(submission, classification, ctx, draft, stage_flags, settings.ambiguity_threshold)
    tracer.event(
        "report.final",
        confidence=report.confidence,
        needs_human_review=report.needs_human_review,
        degraded=report.degraded,
        flags=[f.code for f in report.flags],
    )
    return report
