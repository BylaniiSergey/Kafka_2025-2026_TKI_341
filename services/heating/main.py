"""Сервис нагрева. Автономный."""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import FastAPI


@dataclass
class HeatingSystem:
    active: bool = False
    power_level: float = 0.0
    max_power: float = 1.0
    log: list[str] = field(default_factory=list)

    def set_level(self, level: float) -> None:
        level = max(0.0, min(level, self.max_power))
        self.power_level = level
        self.active = level > 0
        self.log.append(f"Нагрев: {'ВКЛ' if self.active else 'ВЫКЛ'}, мощность={level:.2f}")

    def off(self) -> None:
        self.set_level(0.0)

    def snapshot(self) -> dict:
        return {
            "service": "heating",
            "active": self.active,
            "power_level": self.power_level,
            "log_tail": self.log[-8:],
        }


_mod = HeatingSystem()
app = FastAPI(title="Heating Service", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "heating"}


@app.get("/status")
def status() -> dict:
    return _mod.snapshot()


@app.post("/level")
def level(body: dict) -> dict:
    _mod.set_level(float(body.get("level", 0)))
    return {"ok": True, "state": _mod.snapshot()}


@app.post("/off")
def off() -> dict:
    _mod.off()
    return {"ok": True, "state": _mod.snapshot()}
