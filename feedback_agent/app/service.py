"""Shared entry point used by the CLI and the HTTP API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import RUNS_DIR, Settings
from ..models import Classification, FeedbackReport, FeedbackSubmission
from ..agent.pipeline import run_pipeline
from .render import report_to_markdown
from ..observability.tracing import Tracer


def submission_from_dict(data: Dict[str, Any]) -> FeedbackSubmission:
    return FeedbackSubmission(**data)


def process_submission(
    submission: FeedbackSubmission,
    *,
    out_dir: Optional[Path] = None,
    simulate: Optional[str] = None,
    model: Optional[str] = None,
    verbose: bool = False,
    classification_override: Optional[Classification] = None,
) -> Dict[str, Any]:
    """Run the pipeline, persist artifacts, and return report + markdown + trace events."""
    settings = Settings()
    if model:
        settings.model = model

    root = Path(out_dir) if out_dir else RUNS_DIR
    run_dir = root / submission.feedback_id
    tracer = Tracer(run_dir / "trace.jsonl", verbose=verbose)

    report = run_pipeline(
        submission,
        settings=settings,
        tracer=tracer,
        simulate=simulate,
        classification_override=classification_override,
    )
    markdown = report_to_markdown(report)

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(report.model_dump_json(indent=2))
    (run_dir / "report.md").write_text(markdown)

    return {
        "report": report,
        "markdown": markdown,
        "trace": list(tracer.events),
        "run_dir": str(run_dir),
    }


def load_report(feedback_id: str, out_dir: Optional[Path] = None) -> Optional[FeedbackReport]:
    path = (Path(out_dir) if out_dir else RUNS_DIR) / feedback_id / "report.json"
    if not path.exists():
        return None
    return FeedbackReport.model_validate_json(path.read_text())


def load_trace(feedback_id: str, out_dir: Optional[Path] = None) -> List[dict]:
    path = (Path(out_dir) if out_dir else RUNS_DIR) / feedback_id / "trace.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events
