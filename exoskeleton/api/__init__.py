"""HTTP API (FastAPI) поверх системы управления экзоскелетом."""

from exoskeleton.api.app import app, create_app

__all__ = ["app", "create_app"]
