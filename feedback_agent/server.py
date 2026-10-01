"""Back-compat entrypoint: prefer `feedback_agent.app.server:app`."""

from feedback_agent.app.server import app, create_app

__all__ = ["app", "create_app"]
