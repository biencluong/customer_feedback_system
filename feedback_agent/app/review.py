"""Human-in-the-loop stub: a CS officer approves, overrides, or rejects a report before anything is executed.

The agent only ever *suggests* actions. Nothing is dispatched until a report leaves `pending_review`, and every
decision is appended to an audit log so overrides can later be used as evaluation labels.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..models import FeedbackReport, ReviewDecision, SuggestedAction

AUDIT_LOG_NAME = "review_log.jsonl"


def apply_review(
    report_path: Path,
    decision: str,
    reviewer: str,
    note: Optional[str] = None,
    category: Optional[str] = None,
    urgency: Optional[str] = None,
    actions: Optional[List[str]] = None,
) -> FeedbackReport:
    report = FeedbackReport.model_validate_json(report_path.read_text())
    if report.review.status != "pending_review":
        raise ValueError(f"Report already {report.review.status} by {report.review.reviewer}.")

    overrides = {}
    if decision == "override":
        if category:
            overrides["category"] = {"from": report.classification.category, "to": category}
            report.classification.category = category
        if urgency:
            overrides["urgency"] = {"from": report.classification.urgency, "to": urgency}
            report.classification.urgency = urgency
        if actions:
            overrides["suggested_actions"] = {"from": [a.action for a in report.suggested_actions], "to": actions}
            report.suggested_actions = [
                SuggestedAction(action=a, owner="CS officer", basis=None, requires_approval=False) for a in actions
            ]
        if not overrides:
            raise ValueError("An override needs at least one of --category, --urgency, --action.")

    report.review = ReviewDecision(
        status={"approve": "approved", "override": "overridden", "reject": "rejected"}[decision],
        reviewer=reviewer,
        decided_at=datetime.now(timezone.utc),
        note=note,
        overrides=overrides,
    )
    report_path.write_text(report.model_dump_json(indent=2))

    audit = {
        "feedback_id": report.feedback.feedback_id,
        "decision": report.review.status,
        "reviewer": reviewer,
        "note": note,
        "overrides": overrides,
        "agent_confidence": report.confidence,
        "decided_at": report.review.decided_at.isoformat(),
    }
    with (report_path.parent.parent / AUDIT_LOG_NAME).open("a") as f:
        f.write(json.dumps(audit) + "\n")
    return report


def dispatch_actions(report: FeedbackReport) -> List[str]:
    """Stub for the side-effecting step (create ticket, issue refund, page on-call...). Gated on human approval."""
    if report.review.status not in ("approved", "overridden"):
        return []
    return [f"[stub] would execute: {a.action} (owner: {a.owner})" for a in report.suggested_actions]
