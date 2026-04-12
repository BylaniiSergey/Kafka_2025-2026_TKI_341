"""Сервис охлаждения. Автономный."""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import FastAPI


@dataclass
class CoolingSystem:
    active: bool = False
    fan_speed: float = 0.0
    max_speed: float = 1.0
    log: list[str] = field(default_factory=list)

    def set_speed(self, speed: float) -> None:
        speed = max(0.0, min(speed, self.max_speed))
        self.fan_speed = speed
        self.active = speed > 0
        self.log.append(f"Охлаждение: {'ВКЛ' if self.active else 'ВЫКЛ'}, скорость={speed:.2f}")

    def off(self) -> None:
        self.set_speed(0.0)

    def snapshot(self) -> dict:
        return {
            "service": "cooling",
            "active": self.active,
            "fan_speed": self.fan_speed,
            "log_tail": self.log[-8:],
        }


_mod = CoolingSystem()
app = FastAPI(title="Cooling Service", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "cooling"}


@app.get("/status")
def status() -> dict:
    return _mod.snapshot()


@app.post("/speed")
def speed(body: dict) -> dict:
    _mod.set_speed(float(body.get("speed", 0)))
    return {"ok": True, "state": _mod.snapshot()}


@app.post("/off")
def off() -> dict:
    _mod.off()
    return {"ok": True, "state": _mod.snapshot()}
