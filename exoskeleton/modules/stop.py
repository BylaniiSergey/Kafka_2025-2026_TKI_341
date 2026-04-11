"""Модуль остановки: принудительная остановка и (опционально) открытие экзоскелета."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class StopReason(str, Enum):
    PATIENT_ESTOP = "patient_emergency"
    DOCTOR_ESTOP = "doctor_emergency"
    MONITORING_OBSTACLE = "monitoring_obstacle"
    UNAUTHORIZED_COMMAND = "unauthorized_command"
    LOSS_OF_BALANCE = "loss_of_balance"
    MANUAL_RESET = "manual_reset"


@dataclass
class StopModule:
    """
    Обеспечивает аварийную остановку приводов и фиксирует причину.
    После остановки движение блокируется до явного сброса уполномоченным источником.
    """

    drives_enabled: bool = False
    stopped: bool = False
    last_reason: StopReason | None = None
    last_event_at: datetime | None = None
    log: list[str] = field(default_factory=list)

    def emergency_stop(self, reason: StopReason) -> None:
        """Немедленная остановка всех приводов (сценарий из диаграммы)."""
        self.drives_enabled = False
        self.stopped = True
        self.last_reason = reason
        self.last_event_at = datetime.now(timezone.utc)
        self._log(f"АВАРИЙНАЯ ОСТАНОВКА: {reason.value}")

    def smooth_stop(self) -> None:
        """Плавное завершение сеанса (без флага аварии)."""
        self.drives_enabled = False
        self.stopped = False
        self.last_reason = None
        self.last_event_at = datetime.now(timezone.utc)
        self._log("Плавная остановка приводов, система в режиме готовности")

    def allow_movement(self) -> bool:
        """Разрешить движение после проверок снаружи."""
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
        """Сброс аварийного состояния (упрощённо: только если authorized=True)."""
        if not authorized:
            self._log("Отказ сброса: нет полномочий")
            return False
        self.stopped = False
        self.last_reason = StopReason.MANUAL_RESET
        self.last_event_at = datetime.now(timezone.utc)
        self._log("Аварийный режим сброшен уполномоченным оператором")
        return True

    def is_movement_blocked(self) -> bool:
        return self.stopped or not self.drives_enabled

    def _log(self, message: str) -> None:
        self.log.append(message)
