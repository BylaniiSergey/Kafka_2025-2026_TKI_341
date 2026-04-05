"""Модуль отправки тактильных сигналов пациенту (вибрация, импульсы)."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

class TactilePattern(str, Enum):
    CONTACT_SOLE = "contact_sole"
    WARNING = "warning"
    CUSTOM = "custom"

@dataclass
class TactileModule:
    """
    Формирует тактильную обратную связь. Интенсивность ограничена сверху
    (защита от сценария «болезненная вибрация», угроза 15 в вашей таблице).
    """

    max_intensity: float = 0.85
    last_output: str | None = None
    history: list[str] = field(default_factory=list)

    def emit(
        self,
        pattern: TactilePattern,
        intensity: float,
        *,
        source_trusted: bool,
    ) -> str | None:
        """
        source_trusted: данные о контакте/событии прошли проверку целостности.
        """
        if not source_trusted:
            self._remember("Отказ: источник тактильного сигнала не доверен")
            return None
        clamped = max(0.0, min(float(intensity), self.max_intensity))
        if clamped != intensity:
            self._remember(f"Интенсивность ограничена: {intensity} → {clamped}")
        msg = f"{pattern.value}, интенсивность={clamped:.2f}"
        self.last_output = msg
        self._remember(f"Пациенту: {msg}")
        return msg

    def _remember(self, line: str) -> None:
        self.history.append(line)