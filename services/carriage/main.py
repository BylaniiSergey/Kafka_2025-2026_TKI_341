"""Сервис открытия/закрытия корпуса (коляски). Без зависимостей от других модулей."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from fastapi import FastAPI


class CarriageState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    MOVING = "moving"


@dataclass
class CarriageSystem:
    state: CarriageState = CarriageState.CLOSED
    log: list[str] = field(default_factory=list)

    def request_open(self, *, drives_stopped: bool, emergency: bool = False) -> bool:
        if not emergency and not drives_stopped:
            self._log("Отказ: приводы активны, открытие небезопасно")
            return False
        self.state = CarriageState.OPEN
        self._log("Корпус открыт" + (" (аварийное)" if emergency else ""))
        return True

    def request_close(self) -> bool:
        if self.state == CarriageState.MOVING:
            self._log("Отказ: механизм в движении")
            return False
        self.state = CarriageState.CLOSED
        self._log("Корпус закрыт, пациент зафиксирован")
        return True

    def snapshot(self) -> dict:
        return {"service": "carriage", "state": self.state.value, "log_tail": self.log[-8:]}

    def _log(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {message}")


_mod = CarriageSystem()
app = FastAPI(title="Carriage Service", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "carriage"}


@app.get("/status")
def status() -> dict:
    return _mod.snapshot()


@app.post("/open")
def open_carriage(body: dict) -> dict:
    ok = _mod.request_open(
        drives_stopped=bool(body.get("drives_stopped", True)),
        emergency=bool(body.get("emergency", False)),
    )
    return {"ok": ok, "state": _mod.snapshot()}


@app.post("/close")
def close_carriage() -> dict:
    ok = _mod.request_close()
    return {"ok": ok, "state": _mod.snapshot()}
