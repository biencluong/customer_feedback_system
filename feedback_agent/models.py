from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

Category = Literal[
    "bug_report",
    "billing_issue",
    "feature_request",
    "account_access",
    "churn_risk",
    "abuse",
    "praise",
    "other",
]
Urgency = Literal["low", "medium", "high", "critical"]
Sentiment = Literal["positive", "neutral", "negative"]
Confidence = Literal["high", "medium", "low"]
Channel = Literal["email", "web_form", "chat", "phone", "social", "app_review"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- intake


class FeedbackSubmission(BaseModel):
    feedback_id: str = Field(default_factory=lambda: f"FB-{uuid4().hex[:8]}")
    text: str = Field(min_length=1)
    customer_id: Optional[str] = None
    customer_email: Optional[str] = None
    channel: Channel = "web_form"
    submitted_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------- classification (LLM output schema)


class Classification(BaseModel):
    """Triage classification of a single customer feedback message."""

    category: Category = Field(
        description=(
            "Primary category. bug_report: product malfunction. billing_issue: charges, refunds, pricing. "
            "feature_request: asks for new/changed functionality. account_access: login, password, SSO. "
            "churn_risk: signals intent to cancel or switch to a competitor. abuse: insults, harassment, threats "
            "toward staff (use even if there is also an underlying issue). praise: positive feedback. "
            "other: none of the above or too vague to tell."
        )
    )
    secondary_category: Optional[Category] = Field(
        default=None,
        description="Second plausible category if the message genuinely fits more than one; otherwise null.",
    )
    sentiment: Sentiment
    urgency: Urgency = Field(
        description=(
            "critical: core functionality blocked or security/legal/safety risk. high: significant impact or "
            "money wrongly taken, needs same-day action. medium: real issue but a workaround exists. "
            "low: informational, praise, or nice-to-have."
        )
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Your confidence in the primary category, 0 to 1.")
    rationale: str = Field(description="One or two sentences citing the words in the feedback that drove the decision.")


# ---------------------------------------------------------------- report (LLM output schema)


class Reference(BaseModel):
    source_id: str = Field(description="ID of a policy or guideline returned by a tool, e.g. POL-REFUND-001 or SOP-BILL-01.")
    title: str
    relevance: str = Field(description="One sentence on how this source applies to the case.")


class SuggestedAction(BaseModel):
    action: str = Field(description="Concrete next step for the CS officer.")
    owner: str = Field(description="Who performs it, e.g. CS officer, Billing, Engineering on-call, CS Lead, Security, Legal.")
    basis: Optional[str] = Field(
        default=None, description="source_id of the retrieved policy/guideline that justifies this action, if any."
    )
    requires_approval: bool = Field(description="True if the cited policy requires sign-off beyond the CS officer.")


class ReportDraft(BaseModel):
    """Structured feedback report for a CS officer, grounded only in the provided context."""

    summary: str = Field(description="2-3 sentence neutral summary of what the customer is reporting or asking for.")
    customer_context: str = Field(
        description="Relevant customer facts taken ONLY from the lookup results. If no record was found, say so."
    )
    references: List[Reference] = Field(description="Policies/guidelines actually used. Only IDs present in the context.")
    suggested_actions: List[SuggestedAction]
    confidence: Confidence = Field(description="How confident you are that the suggested actions are correct.")
    uncertainty_notes: List[str] = Field(
        default_factory=list, description="Anything you could not verify or are unsure about. Empty if none."
    )


# ---------------------------------------------------------------- final report + human review


class Flag(BaseModel):
    code: str
    detail: str


class ReviewDecision(BaseModel):
    status: Literal["pending_review", "approved", "overridden", "rejected"] = "pending_review"
    reviewer: Optional[str] = None
    decided_at: Optional[datetime] = None
    note: Optional[str] = None
    overrides: Dict[str, Any] = Field(default_factory=dict)


class FeedbackReport(BaseModel):
    feedback: FeedbackSubmission
    classification: Classification
    summary: str
    customer_found: bool
    customer_context: str
    references: List[Reference]
    suggested_actions: List[SuggestedAction]
    confidence: Confidence
    needs_human_review: bool
    flags: List[Flag]
    sources_consulted: List[str]
    degraded: bool = Field(description="True if any stage fell back to deterministic logic because the LLM failed.")
    generated_at: datetime = Field(default_factory=_utcnow)
    review: ReviewDecision = Field(default_factory=ReviewDecision)
