#!/usr/bin/env python3
"""Regenerate samples/outputs by calling process_submission (same entry path as the UI/API)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from feedback_agent.app.service import process_submission, submission_from_dict  # noqa: E402

SAMPLES = [
    ("01_happy_duplicate_charge", "01_happy_duplicate_charge.json", None),
    ("02_enterprise_critical_bug", "02_enterprise_critical_bug.json", None),
    ("03_ambiguous_vague", "03_ambiguous_vague.json", None),
    ("04_abusive_unknown_customer", "04_abusive_unknown_customer.json", None),
    ("05_feature_request_tool_outage", "05_feature_request_tool_outage.json", "customer_db"),
    ("06_llm_outage", "01_happy_duplicate_charge.json", "llm"),
]


def main() -> None:
    inputs = ROOT / "samples" / "inputs"
    out = ROOT / "samples" / "outputs"
    scratch = out / ".runs"
    out.mkdir(parents=True, exist_ok=True)
    if scratch.exists():
        shutil.rmtree(scratch)

    for name, filename, simulate in SAMPLES:
        print(f"=== {name} ===", file=sys.stderr)
        data = json.loads((inputs / filename).read_text())
        result = process_submission(
            submission_from_dict(data),
            out_dir=scratch / name,
            simulate=simulate,
            verbose=False,
        )
        (out / f"{name}.md").write_text(result["markdown"])
        (out / f"{name}.trace.jsonl").write_text(
            "\n".join(json.dumps(e, default=str) for e in result["trace"]) + "\n"
        )

    shutil.rmtree(scratch)
    print(f"Reports written to {out}/", file=sys.stderr)


if __name__ == "__main__":
    main()
