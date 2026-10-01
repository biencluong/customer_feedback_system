"""Dedicated classification step: one structured-output LLM call, no tools."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from ..models import Classification, FeedbackSubmission
from .prompts import CLASSIFIER_SYSTEM, render_feedback


def classify(llm, submission: FeedbackSubmission) -> Classification:
    structured = llm.with_structured_output(Classification, method="function_calling")
    return structured.invoke([SystemMessage(CLASSIFIER_SYSTEM), HumanMessage(render_feedback(submission))])


def is_ambiguous(c: Classification, threshold: float) -> bool:
    return c.confidence < threshold or c.secondary_category is not None


def fallback_classification(reason: str) -> Classification:
    """Used when the classifier LLM call fails: route to general triage with zero confidence."""
    return Classification(
        category="other",
        secondary_category=None,
        sentiment="neutral",
        urgency="medium",
        confidence=0.0,
        rationale=f"Automatic classification unavailable ({reason}); defaulted to manual triage.",
    )
