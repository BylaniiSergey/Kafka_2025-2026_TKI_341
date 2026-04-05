"""Центральная система управления экзоскелетом (узел «1» на схеме)."""
from __future__ import annotations
from dataclasses import dataclass, field
from exoskeleton.modules.carriage import CarriageSystem
from exoskeleton.modules.climate.cooling import CoolingSystem
from exoskeleton.modules.climate.heating import HeatingSystem
from exoskeleton.modules.climate.temperature import ClimateMode, InternalTemperatureControl
from exoskeleton.modules.stop import StopModule, StopReason
from exoskeleton.modules.tactile import TactileModule, TactilePattern
from exoskeleton.types_common import CommandSource, SystemState

@dataclass
class ExoskeletonControlSystem:
    """
    Связывает модули так, как на высокоуровневой архитектуре:
    команды → контроль → исполнители; мониторинг/телеметрия в прототипе упрощены.
    """

    stop: StopModule = field(default_factory=StopModule)
    carriage: CarriageSystem = field(default_factory=CarriageSystem)
    tactile: TactileModule = field(default_factory=TactileModule)
    temperature: InternalTemperatureControl = field(default_factory=InternalTemperatureControl)
    heating: HeatingSystem = field(default_factory=HeatingSystem)
    cooling: CoolingSystem = field(default_factory=CoolingSystem)

    state: SystemState = SystemState.OFF
    session_active: bool = False

    # Упрощённая «аутентификация»: в реальности — криптомодуль, токены врача и т.д.
    trusted_sources: frozenset[CommandSource] = field(
        default_factory=lambda: frozenset(CommandSource),
    )

    def initialize(self) -> bool:
        """Инициализация и самопроверка (упрощённо)."""
        self.state = SystemState.INITIALIZING
        self.stop.smooth_stop()
        self.heating.off()
        self.cooling.off()
        self.state = SystemState.READY
        return True

    def start_session(self, source: CommandSource) -> bool:
        if not self._is_authentic(source):
            self.stop.emergency_stop(StopReason.UNAUTHORIZED_COMMAND)
            self.state = SystemState.EMERGENCY
            return False
        if self.stop.stopped:
            return False
        self.session_active = True
        self.stop.allow_movement()
        self.state = SystemState.SESSION_ACTIVE
        return True

    def end_session(self, source: CommandSource) -> bool:
        if not self._is_authentic(source):
            return False
        self.session_active = False
        self.stop.smooth_stop()
        self.state = SystemState.READY
        return True

    def emergency_stop(self, source: CommandSource) -> None:
        """Экстренная остановка: пациент, врач/центр/оператор, мониторинг."""
        if source == CommandSource.PATIENT:
            reason = StopReason.PATIENT_ESTOP
        elif source == CommandSource.MONITORING:
            reason = StopReason.MONITORING_OBSTACLE
        else:
            reason = StopReason.DOCTOR_ESTOP
        self.stop.emergency_stop(reason)
        self.session_active = False
        self.state = SystemState.EMERGENCY

    def monitoring_request_stop(self) -> None:
        """Сигнал от системы мониторинга (препятствие, потеря баланса)."""
        self.stop.emergency_stop(StopReason.MONITORING_OBSTACLE)
        self.session_active = False
        self.state = SystemState.EMERGENCY

    def reset_emergency(self, source: CommandSource) -> bool:
        """Сброс аварии только с доверенного канала врача/центра."""
        if source not in (CommandSource.DOCTOR_TABLET, CommandSource.REHAB_CENTER, CommandSource.OPERATOR):
            return False
        ok = self.stop.reset_from_emergency(authorized=True)
        if ok:
            self.state = SystemState.STOPPED
        return ok

    def open_carriage(self, source: CommandSource, *, emergency: bool = False) -> bool:
        if not self._is_authentic(source) and not emergency:
            return False
        drives_stopped = not self.stop.drives_enabled
        return self.carriage.request_open(drives_stopped=drives_stopped, emergency=emergency)

    def close_carriage(self, source: CommandSource) -> bool:
        if not self._is_authentic(source):
            return False
        return self.carriage.request_close()

    def update_climate(self, body_temp_c: float, air_temp_c: float) -> ClimateMode:
        """Обновить датчики и применить нагрев/охлаждение."""
        self.temperature.update_sensors(body_temp_c, air_temp_c)
        mode = self.temperature.decide_mode()
        if mode == ClimateMode.HEATING:
            self.cooling.off()
            self.heating.set_level(0.55)
        elif mode == ClimateMode.COOLING:
            self.heating.off()
            self.cooling.set_speed(0.65)
        else:
            self.heating.off()
            self.cooling.off()
        return mode

    def tactile_from_contact(
        self,
        intensity: float,
        *,
        monitoring_ok: bool,
    ) -> str | None:
        """
        Тактильная обратная связь при опоре (как на расширенной диаграмме).
        monitoring_ok — данные контакта подтверждены цепью мониторинга/сенсоров.
        """
        trusted = monitoring_ok and self.session_active and not self.stop.stopped
        return self.tactile.emit(
            TactilePattern.CONTACT_SOLE,
            intensity,
            source_trusted=trusted,
        )

    def snapshot(self) -> dict[str, object]:
        """Краткий снимок для телеметрии / отчёта преподавателю."""
        return {
            "state": self.state.value,
            "session_active": self.session_active,
            "drives_enabled": self.stop.drives_enabled,
            "stop_stopped": self.stop.stopped,
            "stop_reason": self.stop.last_reason.value if self.stop.last_reason else None,
            "carriage": self.carriage.state.value,
            "climate_mode": self.temperature.mode.value,
            "heating_active": self.heating.active,
            "cooling_active": self.cooling.active,
            "body_temp_c": self.temperature.body_temp_c,
            "air_temp_c": self.temperature.air_temp_c,
            "tactile_last": self.tactile.last_output,
        }

    def _is_authentic(self, source: CommandSource) -> bool:
        return source in self.trusted_sources