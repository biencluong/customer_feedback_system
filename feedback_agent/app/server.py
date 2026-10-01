"""FastAPI server: REST API + static UI for feedback triage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import ROOT_DIR, RUNS_DIR
from ..models import Channel, Classification, FeedbackSubmission
from ..agent.pipeline import SIMULATIONS
from .service import load_report, load_trace, process_submission, submission_from_dict

SAMPLES_DIR = ROOT_DIR / "samples" / "inputs"
WEB_DIR = ROOT_DIR / "web"

SAMPLE_BLURBS = {
    "01_happy_duplicate_charge": "Happy path — Pro customer double-charged",
    "02_enterprise_critical_bug": "Enterprise export outage (critical)",
    "03_ambiguous_vague": "Vague dissatisfaction (ambiguous category)",
    "04_abusive_unknown_customer": "Abusive message + unknown customer",
    "05_feature_request_tool_outage": "Praise + feature request (demo tool outage)",
}


class RunRequest(BaseModel):
    text: str = Field(min_length=1)
    customer_id: Optional[str] = None
    customer_email: Optional[str] = None
    channel: Channel = "web_form"
    feedback_id: Optional[str] = None
    simulate: Optional[str] = None
    model: Optional[str] = None
    # When set, skip the classifier and use this classification for gather + report.
    classification: Optional[Classification] = None


class SampleRunRequest(BaseModel):
    name: str
    simulate: Optional[str] = None
    model: Optional[str] = None
    classification: Optional[Classification] = None


class SampleInfo(BaseModel):
    name: str
    description: str
    path: str
    preview: Dict[str, Any]


def create_app() -> FastAPI:
    app = FastAPI(
        title="Customer Feedback Agent",
        description="Agentic triage: classify → gather context → report",
        version="0.1.0",
    )

    @app.get("/api/health")
    def health() -> dict:
        from ..azure_llm import azure_credentials_configured

        return {
            "ok": True,
            "azure_configured": azure_credentials_configured(),
            "model": os.getenv("AZURE_OPENAI_DEPLOYMENT", os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
            "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            "endpoint_set": bool(os.getenv("AZURE_OPENAI_ENDPOINT")),
            "simulations": list(SIMULATIONS),
        }

    @app.get("/api/samples", response_model=List[SampleInfo])
    def list_samples() -> List[SampleInfo]:
        if not SAMPLES_DIR.exists():
            return []
        items: List[SampleInfo] = []
        for path in sorted(SAMPLES_DIR.glob("*.json")):
            data = json.loads(path.read_text())
            name = path.stem
            items.append(
                SampleInfo(
                    name=name,
                    description=SAMPLE_BLURBS.get(name, name.replace("_", " ")),
                    path=str(path.relative_to(ROOT_DIR)),
                    preview=data,
                )
            )
        return items

    @app.get("/api/samples/{name}")
    def get_sample(name: str) -> dict:
        path = _sample_path(name)
        return json.loads(path.read_text())

    @app.post("/api/run")
    def run_feedback(body: RunRequest) -> dict:
        if body.simulate and body.simulate not in SIMULATIONS:
            raise HTTPException(400, f"simulate must be one of {SIMULATIONS}")
        data: Dict[str, Any] = {
            "text": body.text,
            "customer_id": body.customer_id,
            "customer_email": body.customer_email,
            "channel": body.channel,
        }
        if body.feedback_id:
            data["feedback_id"] = body.feedback_id
        return _run(
            submission_from_dict(data),
            simulate=body.simulate,
            model=body.model,
            classification_override=body.classification,
        )

    @app.post("/api/run/sample")
    def run_sample(body: SampleRunRequest) -> dict:
        if body.simulate and body.simulate not in SIMULATIONS:
            raise HTTPException(400, f"simulate must be one of {SIMULATIONS}")
        data = json.loads(_sample_path(body.name).read_text())
        return _run(
            submission_from_dict(data),
            simulate=body.simulate,
            model=body.model,
            classification_override=body.classification,
        )

    @app.post("/api/run/upload")
    async def run_upload(
        file: UploadFile = File(...),
        simulate: Optional[str] = Form(None),
        model: Optional[str] = Form(None),
    ) -> dict:
        if simulate and simulate not in SIMULATIONS:
            raise HTTPException(400, f"simulate must be one of {SIMULATIONS}")
        if not file.filename or not file.filename.lower().endswith(".json"):
            raise HTTPException(400, "Upload a .json file matching the sample input schema.")
        raw = await file.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise HTTPException(400, f"Invalid JSON: {e}") from e
        if not isinstance(data, dict) or "text" not in data:
            raise HTTPException(400, 'JSON must be an object with at least a "text" field.')
        try:
            submission = submission_from_dict(data)
        except Exception as e:
            raise HTTPException(400, f"Invalid submission: {e}") from e
        return _run(submission, simulate=simulate or None, model=model or None)

    @app.get("/api/runs/{feedback_id}")
    def get_run(feedback_id: str) -> dict:
        report = load_report(feedback_id)
        if report is None:
            raise HTTPException(404, f"No run found for {feedback_id}")
        from .render import report_to_markdown

        return {
            "report": report.model_dump(mode="json"),
            "markdown": report_to_markdown(report),
            "trace": load_trace(feedback_id),
            "run_dir": str(RUNS_DIR / feedback_id),
        }

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app


def _sample_path(name: str) -> Path:
    safe = Path(name).name
    if safe.endswith(".json"):
        safe = safe[:-5]
    path = SAMPLES_DIR / f"{safe}.json"
    if not path.exists():
        raise HTTPException(404, f"Sample not found: {safe}")
    return path


def _run(
    submission: FeedbackSubmission,
    *,
    simulate: Optional[str],
    model: Optional[str],
    classification_override: Optional[Classification] = None,
) -> dict:
    result = process_submission(
        submission,
        simulate=simulate,
        model=model,
        verbose=False,
        classification_override=classification_override,
    )
    report = result["report"]
    return {
        "report": report.model_dump(mode="json"),
        "markdown": result["markdown"],
        "trace": result["trace"],
        "run_dir": result["run_dir"],
    }


app = create_app()
