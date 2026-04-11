"""Система нагревания (термоодежда / нагревательные элементы)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HeatingSystem:
    """Включается при низкой температуре внутри костюма (по решению контроллера)."""

    active: bool = False
    power_level: float = 0.0  # 0..1
    max_power: float = 1.0
    log: list[str] = field(default_factory=list)

    def set_level(self, level: float) -> None:
        level = max(0.0, min(level, self.max_power))
        self.power_level = level
        self.active = level > 0
        self.log.append(f"Нагрев: {'ВКЛ' if self.active else 'ВЫКЛ'}, мощность={level:.2f}")

    def off(self) -> None:
        self.set_level(0.0)
