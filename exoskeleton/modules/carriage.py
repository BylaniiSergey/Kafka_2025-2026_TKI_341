"""Система открытия и закрытия корпуса (коляски), где размещается пациент."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class CarriageState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    MOVING = "moving"


@dataclass
class CarriageSystem:
    """
    Управляет открытием/закрытием посадочного модуля.
    В аварии открытие должно оставаться возможным (против угрозы «блокировка выхода»).
    """

    state: CarriageState = CarriageState.CLOSED
    log: list[str] = field(default_factory=list)

    def request_open(
        self,
        *,
        drives_stopped: bool,
        emergency: bool = False,
    ) -> bool:
        """
        Открыть корпус. В обычном режиме — только при остановленных приводах.
        При emergency=True — разрешено даже если приводы не в безопасном состоянии
        (упрощённая политика «последний шанс эвакуации» — в реальном изделии — отдельная цепь).
        """
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

    def _log(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {message}")
