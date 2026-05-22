import os
import sys
import time
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

BASE_ENV = os.environ.copy()
BASE_ENV["KAFKA_ENABLED"]            = "true"
BASE_ENV["KAFKA_BOOTSTRAP_SERVERS"] = "127.0.0.1:9092"
BASE_ENV["PYTHONPATH"]               = str(ROOT)

SERVICES = [
    # Infra / security
    ("crypto_module",               4001),
    ("critical_battery_monitor",    4002),
    ("critical_sensors",            4003),
    ("task_orchestrator",           5000),
    ("emergency_control_module",    5001),
    ("emergency_open_module",       5002),
    ("emergency_stop_module",       5003),
    ("tactile_verification_module", 5004),
    ("position_check_module",       5005),
    ("gnss_navigation_module",      5006),
    ("ins_navigation_module",       5007),
    ("command_verification",        5101),
    ("critical_situation_recognition", 5102),
    ("sensor_verification",         5103),
    # Monitoring / battery / sensors
    ("comms_module",                6001),
    ("monitoring_system",           6002),
    ("sensors_module",              6003),
    ("battery_controller",          6004),
    ("charger_module",              6005),
    ("battery_cell",                6006),
    # Auxiliary
    ("stop_module",                 7001),
    ("carriage_system",             7002),
    ("temperature_system",          7003),
    ("heating_system",              7004),
    ("cooling_system",              7005),
    ("tactile_system",              7006),
    # Critical / safety
    ("critical_sensors_arms",       7101),
    ("critical_sensors_legs",       7102),
    ("neural_verify_upper",         7103),
    ("neural_verify_lower",         7104),
    ("temperature_measurement_system", 7105),
    ("arm_force_limits_system",     7106),
    # Arms
    ("neural_signal_system",        8001),
    ("arm_movement_system",         8002),
    ("upper_arm_system",            8003),
    ("middle_arm_system",           8004),
    ("fingers_system",              8005),
    ("force_control_system",        8006),
    # Legs
    ("leg_neural_signal_system",    9001),
    ("leg_movement_system",         9002),
    ("knee_belt_system",            9003),
    ("track_system",                9004),
    ("leg_force_control_system",    9006),
    ("leg_force_limits_system",     9105),
    # Main control — запускается последним
    ("control_system",              8000),
]

processes: list[tuple[str, subprocess.Popen]] = []


# ── Ожидание Kafka ────────────────────────────────────────────────────────────

def wait_for_kafka(
    host: str    = "localhost",
    port: int    = 9092,
    timeout: int = 120,
) -> bool:
    """
    Ждёт пока порт Kafka станет доступным.

    Это TCP-проверка (не протокольная), поэтому после успеха
    добавляем дополнительную паузу для завершения инициализации брокера.
    """
    print(f"\nОжидание Kafka на {host}:{port} (таймаут {timeout}s)...")
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                # Порт открыт — даём брокеру время завершить инициализацию
                print("Kafka порт доступен. Ждём 8s для завершения инициализации...")
                time.sleep(8)
                print("Kafka готова!\n")
                return True
        except OSError:
            remaining = int(deadline - time.monotonic())
            print(f"  Kafka ещё не готова... ({remaining}s осталось)", end='\r')
            time.sleep(2)

    print(f"\nОШИБКА: Kafka не стала доступна за {timeout}s")
    print("Проверьте: docker compose up -d && docker compose logs kafka")
    return False


# ── Управление сервисами ──────────────────────────────────────────────────────

def get_script_path(module_name: str) -> Path:
    return ROOT / module_name / "main.py"


def start_service(module_name: str, port: int) -> bool:
    script_path = get_script_path(module_name)

    if not script_path.exists():
        print(f"SKIP  {module_name:<38} main.py не найден")
        return False

    print(f"START {module_name:<38} :{port}")

    try:
        proc = subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(ROOT),
            env=BASE_ENV,
        )
    except Exception as e:
        print(f"FAIL  {module_name:<38} не удалось запустить: {e}")
        return False

    # Даём процессу время инициализироваться
    time.sleep(1.2)

    if proc.poll() is not None:
        print(f"FAIL  {module_name:<38} завершился сразу")
        return False

    processes.append((module_name, proc))
    return True


def stop_service(name: str, proc: subprocess.Popen):
    try:
        proc.terminate()
        proc.wait(timeout=5)
        print(f"STOP  {name:<38}")
    except subprocess.TimeoutExpired:
        proc.kill()
        print(f"KILL  {name:<38}")
    except Exception:
        pass


def stop_all():
    print("\nОстановка сервисов...\n")
    # Останавливаем в обратном порядке (control_system первым)
    for name, proc in reversed(processes):
        stop_service(name, proc)


def print_summary(started: int, total: int):
    print("\n" + "=" * 74)
    print(f"Запущено сервисов: {started}/{total}")
    print()
    print("Документация API:")
    print("  Основная система:       http://localhost:8000/docs")
    print("  Мониторинг:             http://localhost:6002/docs")
    print("  Датчики:                http://localhost:6003/docs")
    print("  Оркестратор задач:      http://localhost:5000/docs")
    print("  Верификация команд:     http://localhost:5101/docs")
    print("  Аварийное управление:   http://localhost:5001/docs")
    print("=" * 74)


# ── Точка входа ───────────────────────────────────────────────────────────────

def main():
    # Шаг 1: Ждём Kafka перед запуском любых сервисов
    if not wait_for_kafka():
        print("Запуск отменён: Kafka недоступна.")
        sys.exit(1)

    started = 0
    total   = len(SERVICES)

    try:
        # Шаг 2: Запускаем все сервисы последовательно
        for module_name, port in SERVICES:
            if start_service(module_name, port):
                started += 1

        print_summary(started, total)
        input("\nНажмите Enter для остановки всех сервисов...\n")

    except KeyboardInterrupt:
        print("\nПрерывание пользователем.")

    finally:
        stop_all()


if __name__ == "__main__":
    main()