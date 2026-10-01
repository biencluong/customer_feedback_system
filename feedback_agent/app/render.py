from __future__ import annotations

from ..models import FeedbackReport


def report_to_markdown(r: FeedbackReport) -> str:
    c = r.classification
    fb = r.feedback
    lines = [
        f"# Feedback Report {fb.feedback_id}",
        "",
        f"**Review status:** {r.review.status}  |  **Confidence:** {r.confidence}  |  "
        f"**Needs careful review:** {'YES' if r.needs_human_review else 'no'}"
        + ("  |  **DEGRADED (LLM fallback used)**" if r.degraded else ""),
        "",
        "## Input",
        f"- Customer: `{fb.customer_id or '-'}` / `{fb.customer_email or '-'}`  |  Channel: {fb.channel}  |  "
        f"Submitted: {fb.submitted_at.isoformat()}",
        "",
        "> " + fb.text.replace("\n", "\n> "),
        "",
        "## Classification",
        f"- **Category:** {c.category}" + (f" (secondary: {c.secondary_category})" if c.secondary_category else ""),
        f"- **Urgency:** {c.urgency}  |  **Sentiment:** {c.sentiment}  |  **Classifier confidence:** {c.confidence:.2f}",
        f"- **Rationale:** {c.rationale}",
        "",
        "## Summary",
        r.summary,
        "",
        "## Customer context" + ("" if r.customer_found else " (NO RECORD FOUND)"),
        r.customer_context,
        "",
        "## Policy / guideline references",
    ]
    lines += [f"- `{ref.source_id}` {ref.title}: {ref.relevance}" for ref in r.references] or ["- none"]
    lines += ["", "## Suggested next actions"]
    for i, a in enumerate(r.suggested_actions, 1):
        basis = f" [basis: `{a.basis}`]" if a.basis else ""
        approval = " **(requires approval)**" if a.requires_approval else ""
        lines.append(f"{i}. {a.action} (owner: {a.owner}){basis}{approval}")
    lines += ["", "## Flags"]
    lines += [f"- **{f.code}**: {f.detail}" for f in r.flags] or ["- none"]
    if r.review.status != "pending_review":
        lines += [
            "",
            "## Human review",
            f"- {r.review.status} by {r.review.reviewer} at {r.review.decided_at}",
            f"- Note: {r.review.note or '-'}",
        ]
        if r.review.overrides:
            lines.append(f"- Overrides: {r.review.overrides}")
    return "\n".join(lines) + "\n"
