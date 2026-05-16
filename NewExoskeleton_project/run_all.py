import os
import sys
import time
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

BASE_ENV = os.environ.copy()
BASE_ENV["KAFKA_ENABLED"] = "true"
BASE_ENV["KAFKA_BOOTSTRAP_SERVERS"] = "localhost:9092"
BASE_ENV["PYTHONPATH"] = str(ROOT)

SERVICES = [
    # =========================
    # Infra / security / bus-adjacent
    # =========================
    ("crypto_module", 4001),
    ("critical_battery_monitor", 4002),
    ("critical_sensors", 4003),

    ("task_orchestrator", 5000),
    ("emergency_control_module", 5001),
    ("emergency_open_module", 5002),
    ("emergency_stop_module", 5003),
    ("tactile_verification_module", 5004),
    ("position_check_module", 5005),
    ("gnss_navigation_module", 5006),
    ("ins_navigation_module", 5007),

    # Перенесённые конфликтующие сервисы
    ("command_verification", 5101),
    ("critical_situation_recognition", 5102),
    ("sensor_verification", 5103),

    # =========================
    # Monitoring / battery / sensors
    # =========================
    ("comms_module", 6001),
    ("monitoring_system", 6002),
    ("sensors_module", 6003),
    ("battery_controller", 6004),
    ("charger_module", 6005),
    ("battery_cell", 6006),

    # =========================
    # Auxiliary
    # =========================
    ("stop_module", 7001),
    ("carriage_system", 7002),
    ("temperature_system", 7003),
    ("heating_system", 7004),
    ("cooling_system", 7005),
    ("tactile_system", 7006),

    # =========================
    # Critical / safety additions
    # =========================
    ("critical_sensors_arms", 7101),
    ("critical_sensors_legs", 7102),
    ("neural_verify_upper", 7103),
    ("neural_verify_lower", 7104),
    ("temperature_measurement_system", 7105),
    ("arm_force_limits_system", 7106),

    # =========================
    # Arms
    # =========================
    ("neural_signal_system", 8001),
    ("arm_movement_system", 8002),
    ("upper_arm_system", 8003),
    ("middle_arm_system", 8004),
    ("fingers_system", 8005),
    ("force_control_system", 8006),

    # =========================
    # Legs
    # =========================
    ("leg_neural_signal_system", 9001),
    ("leg_movement_system", 9002),
    ("knee_belt_system", 9003),
    ("track_system", 9004),
    ("leg_force_control_system", 9006),
    ("leg_force_limits_system", 9105),

    # =========================
    # Main control last
    # =========================
    ("control_system", 8000),
]

processes = []


def get_script_path(module_name: str) -> Path:
    return ROOT / module_name / "main.py"


def start_service(module_name: str, port: int) -> bool:
    script_path = get_script_path(module_name)

    if not script_path.exists():
        print(f"SKIP  {module_name:<34} main.py not found")
        return False

    print(f"START {module_name:<34} :{port}")

    try:
        proc = subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(ROOT),
            env=BASE_ENV,
        )
    except Exception as e:
        print(f"FAIL  {module_name:<34} cannot start: {e}")
        return False

    time.sleep(1.2)

    if proc.poll() is not None:
        print(f"FAIL  {module_name:<34} exited immediately")
        return False

    processes.append((module_name, proc))
    return True


def stop_service(name: str, proc: subprocess.Popen):
    try:
        proc.terminate()
        proc.wait(timeout=5)
        print(f"STOP  {name:<34}")
    except Exception:
        try:
            proc.kill()
            print(f"KILL  {name:<34}")
        except Exception:
            pass


def stop_all():
    print("\nStopping services...\n")
    for name, proc in reversed(processes):
        stop_service(name, proc)


def print_summary(started_count: int, total_count: int):
    print("\n" + "=" * 74)
    print(f"Started services: {started_count}/{total_count}")
    print("Main docs:                 http://localhost:8000/docs")
    print("Monitoring docs:           http://localhost:6002/docs")
    print("Sensors docs:              http://localhost:6003/docs")
    print("Task orchestrator docs:    http://localhost:5000/docs")
    print("Command verification docs: http://localhost:5101/docs")
    print("Emergency control docs:    http://localhost:5001/docs")
    print("=" * 74)


def main():
    started = 0
    total = len(SERVICES)

    try:
        for module_name, port in SERVICES:
            if start_service(module_name, port):
                started += 1

        print_summary(started, total)
        input("\nPress Enter to stop all services...\n")

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        stop_all()


if __name__ == "__main__":
    main()