"""Контроль температуры внутренней части костюма (тело / воздух)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ClimateMode(str, Enum):
    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"


@dataclass
class InternalTemperatureControl:
    """
    Считывает показатели и решает, включать нагрев или охлаждение.
    Простая проверка достоверности: диапазон физиологически возможных значений
    (грубая защита от подмены датчика в прототипе).
    """

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
        """Возвращает False, если показания отбракованы."""
        ok = (
            self.body_min <= body_c <= self.body_max
            and self.air_min <= air_c <= self.air_max
        )
        self.sensor_trusted = ok
        if ok:
            self.body_temp_c = body_c
            self.air_temp_c = air_c
            self.log.append(f"Датчики ОК: тело={body_c:.1f}°C, воздух={air_c:.1f}°C")
        else:
            self.log.append("Тревога: показания вне допустимого диапазона — к климату не верим")
        return ok

    def decide_mode(self) -> ClimateMode:
        """Выбор режима без прямого управления исполнителями (делает control system)."""
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
