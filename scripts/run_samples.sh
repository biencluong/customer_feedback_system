#!/usr/bin/env bash
# Regenerates sample reports via the shared service layer (same path the UI/API uses).
# Sample 05 simulates a customer-DB outage; sample 06 simulates full LLM outage.
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/run_samples.py
