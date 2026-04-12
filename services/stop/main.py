"""
Сервис «Модуль остановки» — только своя логика + FastAPI, без импортов других модулей проекта.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from fastapi import FastAPI

# --- домен (самодостаточная копия для микросервиса) ---


class StopReason(str, Enum):
    PATIENT_ESTOP = "patient_emergency"
    DOCTOR_ESTOP = "doctor_emergency"
    MONITORING_OBSTACLE = "monitoring_obstacle"
    UNAUTHORIZED_COMMAND = "unauthorized_command"
    LOSS_OF_BALANCE = "loss_of_balance"
    MANUAL_RESET = "manual_reset"


@dataclass
class StopModule:
    drives_enabled: bool = False
    stopped: bool = False
    last_reason: StopReason | None = None
    last_event_at: datetime | None = None
    log: list[str] = field(default_factory=list)

    def emergency_stop(self, reason: StopReason) -> None:
        self.drives_enabled = False
        self.stopped = True
        self.last_reason = reason
        self.last_event_at = datetime.now(timezone.utc)
        self._log(f"АВАРИЙНАЯ ОСТАНОВКА: {reason.value}")

    def smooth_stop(self) -> None:
        self.drives_enabled = False
        self.stopped = False
        self.last_reason = None
        self.last_event_at = datetime.now(timezone.utc)
        self._log("Плавная остановка приводов, система в режиме готовности")

    def allow_movement(self) -> bool:
        if self.stopped and self.last_reason not in (StopReason.MANUAL_RESET,):
            self._log("Приводы не включены: активна аварийная остановка")
            return False
        self.drives_enabled = True
        self.stopped = False
        self.last_reason = None
        self.last_event_at = datetime.now(timezone.utc)
        self._log("Приводы разрешены")
        return True

    def reset_from_emergency(self, authorized: bool) -> bool:
        if not authorized:
            self._log("Отказ сброса: нет полномочий")
            return False
        self.stopped = False
        self.last_reason = StopReason.MANUAL_RESET
        self.last_event_at = datetime.now(timezone.utc)
        self._log("Аварийный режим сброшен уполномоченным оператором")
        return True

    def snapshot(self) -> dict:
        return {
            "service": "stop",
            "drives_enabled": self.drives_enabled,
            "stopped": self.stopped,
            "last_reason": self.last_reason.value if self.last_reason else None,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "log_tail": self.log[-8:],
        }

    def _log(self, message: str) -> None:
        self.log.append(message)


_mod = StopModule()
app = FastAPI(title="Stop Module Service", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "stop"}


@app.get("/status")
def status() -> dict:
    return _mod.snapshot()


@app.post("/emergency-stop")
def emergency_stop(body: dict) -> dict:
    reason = StopReason(body.get("reason", "patient_emergency"))
    _mod.emergency_stop(reason)
    return {"ok": True, "state": _mod.snapshot()}


@app.post("/smooth-stop")
def smooth_stop() -> dict:
    _mod.smooth_stop()
    return {"ok": True, "state": _mod.snapshot()}


@app.post("/allow-movement")
def allow_movement() -> dict:
    ok = _mod.allow_movement()
    return {"ok": ok, "state": _mod.snapshot()}


@app.post("/reset-emergency")
def reset_emergency(body: dict) -> dict:
    ok = _mod.reset_from_emergency(bool(body.get("authorized", False)))
    return {"ok": ok, "state": _mod.snapshot()}
