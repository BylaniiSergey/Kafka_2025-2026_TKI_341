"""Система охлаждения (вентиляторы / Пельтье)."""
from __future__ import annotations
from dataclasses import dataclass, field

@dataclass
class CoolingSystem:
    """Используется при активной работе или жаркой погоде."""

    active: bool = False
    fan_speed: float = 0.0  # 0..1
    max_speed: float = 1.0
    log: list[str] = field(default_factory=list)

    def set_speed(self, speed: float) -> None:
        speed = max(0.0, min(speed, self.max_speed))
        self.fan_speed = speed
        self.active = speed > 0
        self.log.append(f"Охлаждение: {'ВКЛ' if self.active else 'ВЫКЛ'}, скорость={speed:.2f}")

    def off(self) -> None:
        self.set_speed(0.0)