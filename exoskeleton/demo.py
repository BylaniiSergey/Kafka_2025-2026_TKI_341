"""
Демонстрация сценариев для преподавателя.
Запуск: python -m exoskeleton.demo
"""

from __future__ import annotations

from exoskeleton.control_system import ExoskeletonControlSystem
from exoskeleton.types_common import CommandSource


def _print(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def main() -> None:
    ctrl = ExoskeletonControlSystem()

    _print("1. Инициализация")
    ctrl.initialize()
    print(ctrl.snapshot())

    _print("2. Сеанс движения + тактильная обратная связь (опора)")
    ctrl.start_session(CommandSource.PATIENT)
    msg = ctrl.tactile_from_contact(0.4, monitoring_ok=True)
    print("Тактильный канал:", msg)
    print(ctrl.snapshot())

    _print("3. Климат: жарко → охлаждение")
    ctrl.update_climate(body_temp_c=37.5, air_temp_c=30.0)
    print(ctrl.snapshot())

    _print("4. Климат: холодно → нагрев")
    ctrl.update_climate(body_temp_c=35.0, air_temp_c=16.0)
    print(ctrl.snapshot())

    _print("5. Норма → климат выключен")
    ctrl.update_climate(body_temp_c=36.6, air_temp_c=22.0)
    print(ctrl.snapshot())

    _print("6. Экстренная остановка пациента")
    ctrl.emergency_stop(CommandSource.PATIENT)
    print(ctrl.snapshot())

    _print("7. Открытие корпуса после остановки (эвакуация)")
    ctrl.open_carriage(CommandSource.PATIENT)
    print(ctrl.snapshot())

    _print("8. Сброс аварии врачом, закрытие корпуса")
    ctrl.reset_emergency(CommandSource.DOCTOR_TABLET)
    ctrl.close_carriage(CommandSource.DOCTOR_TABLET)
    print(ctrl.snapshot())

    _print("9. Недоверенные источники → отказ и стоп (аналог ЦБ1)")
    bad = ExoskeletonControlSystem(trusted_sources=frozenset())
    bad.initialize()
    bad.start_session(CommandSource.PATIENT)
    print(bad.snapshot())

    _print("10. Подмена датчика температуры (вне диапазона)")
    ctrl2 = ExoskeletonControlSystem()
    ctrl2.initialize()
    ctrl2.update_climate(body_temp_c=80.0, air_temp_c=22.0)
    print("Датчик доверен:", ctrl2.temperature.sensor_trusted)
    print(ctrl2.snapshot())

    print()
    print("Готово. Модули: стоп, коляска/корпус, тактиль, температура, нагрев, охлаждение.")
    print()


if __name__ == "__main__":
    main()
