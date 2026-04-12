"""Сервис контроля температуры внутренней части (только расчёт режима)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from fastapi import FastAPI


class ClimateMode(str, Enum):
    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"


@dataclass
class InternalTemperatureControl:
    body_temp_c: float = 36.6
    air_temp_c: float = 22.0
    body_min: float = 30.0
    body_max: float = 42.0
    air_min: float = 5.0
    air_max: float = 50.0
    target_body_low: float = 35.5
    target_body_high: float = 37.2
    target_air_high: float = 28.0
    target_air_low: float = 18.0
    sensor_trusted: bool = True
    mode: ClimateMode = ClimateMode.IDLE
    log: list[str] = field(default_factory=list)

    def update_sensors(self, body_c: float, air_c: float) -> bool:
        ok = self.body_min <= body_c <= self.body_max and self.air_min <= air_c <= self.air_max
        self.sensor_trusted = ok
        if ok:
            self.body_temp_c = body_c
            self.air_temp_c = air_c
            self.log.append(f"Датчики ОК: тело={body_c:.1f}°C, воздух={air_c:.1f}°C")
        else:
            self.log.append("Тревога: показания вне допустимого диапазона — к климату не верим")
        return ok

    def decide_mode(self) -> ClimateMode:
        if not self.sensor_trusted:
            self.mode = ClimateMode.IDLE
            return self.mode
        if self.body_temp_c < self.target_body_low or self.air_temp_c < self.target_air_low:
            self.mode = ClimateMode.HEATING
        elif self.body_temp_c > self.target_body_high or self.air_temp_c > self.target_air_high:
            self.mode = ClimateMode.COOLING
        else:
            self.mode = ClimateMode.IDLE
        self.log.append(f"Режим климата: {self.mode.value}")
        return self.mode

    def snapshot(self) -> dict:
        return {
            "service": "temperature",
            "body_temp_c": self.body_temp_c,
            "air_temp_c": self.air_temp_c,
            "sensor_trusted": self.sensor_trusted,
            "mode": self.mode.value,
            "log_tail": self.log[-8:],
        }


_mod = InternalTemperatureControl()
app = FastAPI(title="Temperature Service", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "temperature"}


@app.get("/status")
def status() -> dict:
    return _mod.snapshot()


@app.post("/sensors")
def sensors(body: dict) -> dict:
    ok = _mod.update_sensors(float(body["body_temp_c"]), float(body["air_temp_c"]))
    return {"ok": ok, "state": _mod.snapshot()}


@app.post("/decide")
def decide() -> dict:
    mode = _mod.decide_mode()
    return {"ok": True, "climate_mode": mode.value, "state": _mod.snapshot()}
