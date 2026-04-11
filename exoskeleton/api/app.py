"""
REST API: команды в формате JSON (как в messaging) + телеметрия.
Запуск: uvicorn exoskeleton.api.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse

from exoskeleton.control_system import ExoskeletonControlSystem
from exoskeleton.messaging.json_handler import CommandJsonHandler


def create_app(control: ExoskeletonControlSystem | None = None) -> FastAPI:
    app = FastAPI(
        title="Exoskeleton Control API",
        description="Прототип: система управления экзоскелетом + JSON-команды.",
        version="0.1.0",
    )
    ctrl = control or ExoskeletonControlSystem()
    app.state.exo_control = ctrl
    app.state.exo_handler = CommandJsonHandler(ctrl)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/telemetry")
    def telemetry(request: Request) -> dict[str, Any]:
        ctrl: ExoskeletonControlSystem = request.app.state.exo_control
        return ctrl.snapshot()

    @app.post("/commands")
    def run_command(
        request: Request,
        body: Annotated[dict[str, Any], Body(...)],
    ) -> JSONResponse:
        handler: CommandJsonHandler = request.app.state.exo_handler
        result = handler.handle(body)
        status = 200 if result.get("ok") else 422
        return JSONResponse(content=result, status_code=status)

    return app


app = create_app()
