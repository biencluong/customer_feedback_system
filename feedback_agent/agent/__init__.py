"""Agent stages: classify → gather context (tools) → report."""

from .pipeline import SIMULATIONS, run_pipeline

__all__ = ["SIMULATIONS", "run_pipeline"]
