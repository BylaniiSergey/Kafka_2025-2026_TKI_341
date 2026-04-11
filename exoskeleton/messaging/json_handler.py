"""
Разбор JSON-команд и вызов методов ExoskeletonControlSystem без изменения логики модулей.
"""

from __future__ import annotations

import json
from typing import Any

from exoskeleton.control_system import ExoskeletonControlSystem
from exoskeleton.types_common import CommandSource


def _source(raw: str) -> CommandSource:
    try:
        return CommandSource(raw)
    except ValueError as e:
        raise ValueError(f"invalid source: {raw!r}") from e


class CommandJsonHandler:
    """Один вход: dict или JSON-строка; один выход: dict для ответа/телеметрии."""

    def __init__(self, control: ExoskeletonControlSystem) -> None:
        self._ctrl = control

    def handle(self, payload: dict[str, Any] | str) -> dict[str, Any]:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as e:
                return self._fail(None, f"invalid_json: {e}")
        if not isinstance(payload, dict):
            return self._fail(None, "payload_must_be_object")
        cid = payload.get("correlation_id")
        action = payload.get("action")
        if not action or not isinstance(action, str):
            return self._fail(cid, "missing_action")

        try:
            event: dict[str, Any] | None = None
            if action == "initialize":
                ok = self._ctrl.initialize()
                return self._ok(cid, ok=ok, extra={"initialized": ok})
            if action == "start_session":
                ok = self._ctrl.start_session(_source(str(payload["source"])))
                return self._ok(cid, ok=ok)
            if action == "end_session":
                ok = self._ctrl.end_session(_source(str(payload["source"])))
                return self._ok(cid, ok=ok)
            if action == "emergency_stop":
                self._ctrl.emergency_stop(_source(str(payload["source"])))
                event = {"type": "emergency_stop", "source": payload.get("source")}
                return self._ok(cid, ok=True, event=event)
            if action == "monitoring_stop":
                self._ctrl.monitoring_request_stop()
                event = {"type": "monitoring_stop"}
                return self._ok(cid, ok=True, event=event)
            if action == "reset_emergency":
                ok = self._ctrl.reset_emergency(_source(str(payload["source"])))
                return self._ok(cid, ok=ok)
            if action == "open_carriage":
                ok = self._ctrl.open_carriage(
                    _source(str(payload["source"])),
                    emergency=bool(payload.get("emergency", False)),
                )
                return self._ok(cid, ok=ok)
            if action == "close_carriage":
                ok = self._ctrl.close_carriage(_source(str(payload["source"])))
                return self._ok(cid, ok=ok)
            if action == "update_climate":
                mode = self._ctrl.update_climate(
                    float(payload["body_temp_c"]),
                    float(payload["air_temp_c"]),
                )
                return self._ok(cid, ok=True, extra={"climate_mode": mode.value})
            if action == "tactile_contact":
                msg = self._ctrl.tactile_from_contact(
                    float(payload.get("intensity", 0.5)),
                    monitoring_ok=bool(payload.get("monitoring_ok", False)),
                )
                return self._ok(cid, ok=True, extra={"tactile": msg})
            if action == "telemetry" or action == "snapshot":
                return self._ok(cid, ok=True)
            return self._fail(cid, f"unknown_action:{action}")
        except (KeyError, ValueError, TypeError) as e:
            return self._fail(cid, f"bad_payload:{e}")

    def _ok(
        self,
        correlation_id: Any,
        *,
        ok: bool,
        extra: dict[str, Any] | None = None,
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": ok,
            "correlation_id": correlation_id,
            "snapshot": self._ctrl.snapshot(),
        }
        if extra:
            out["result"] = extra
        if event:
            out["event"] = event
        return out

    def _fail(self, correlation_id: Any, error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "correlation_id": correlation_id,
            "error": error,
            "snapshot": self._ctrl.snapshot(),
        }
