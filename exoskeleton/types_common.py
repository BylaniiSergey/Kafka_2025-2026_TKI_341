"""Общие типы: источники команд, роли (упрощённая модель для прототипа)."""

from __future__ import annotations

from enum import Enum


class CommandSource(str, Enum):
    """Кто отдал команду (для трассировки и упрощённой проверки прав)."""

    PATIENT = "patient"
    DOCTOR_TABLET = "doctor_tablet"
    REHAB_CENTER = "rehab_center"
    OPERATOR = "operator"
    MONITORING = "monitoring"


class SystemState(str, Enum):
    """Глобальное состояние системы управления."""

    OFF = "off"
    INITIALIZING = "initializing"
    READY = "ready"
    SESSION_ACTIVE = "session_active"
    STOPPED = "stopped"
    EMERGENCY = "emergency"
