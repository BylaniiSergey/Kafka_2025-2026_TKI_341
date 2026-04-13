# run_all.py
import subprocess
import sys
import time
import os

SERVICES = [
    # Батарея (нет зависимостей)
    ('battery_cell', 6006),
    ('charger_module', 6005),
    # Батарея контроллер (зависит от cell + charger)
    ('battery_controller', 6004),
    # Датчики (нет зависимостей)
    ('sensors_module', 6003),
    # Мониторинг (зависит от sensors + battery_controller)
    ('monitoring_system', 6002),
    # Связь (зависит от monitoring)
    ('comms_module', 6001),
    # Вспомогательные
    ('stop_module', 7001),
    ('carriage_system', 7002),
    ('temperature_system', 7003),
    ('heating_system', 7004),
    ('cooling_system', 7005),
    ('tactile_system', 7006),
    # Руки
    ('force_control_system', 8006),
    ('upper_arm_system', 8003),
    ('middle_arm_system', 8004),
    ('fingers_system', 8005),
    ('arm_movement_system', 8002),
    ('neural_signal_system', 8001),
    # Ноги
    ('leg_force_control_system', 9006),
    ('knee_belt_system', 9003),
    ('track_system', 9004),
    ('leg_movement_system', 9002),
    ('leg_neural_signal_system', 9001),
    # Главный (последним)
    ('control_system', 8000),
]

processes = []


def start_all():
    for folder, port in SERVICES:
        main_path = os.path.join(folder, 'main.py')
        if not os.path.exists(main_path):
            print(f"SKIP {folder} — main.py not found")
            continue
        print(f"Starting {folder} on port {port}...")
        proc = subprocess.Popen([sys.executable, main_path])
        processes.append((folder, proc))
        time.sleep(1)


    print("\n" + "=" * 60)
    print("All 24 services started!")
    print("Control panel: http://localhost:8000/docs")
    print("=" * 60)


def stop_all():
    for folder, proc in processes:
        print(f"Stopping {folder}...")
        proc.terminate()


if __name__ == '__main__':
    try:
        start_all()
        input("\nPress Enter to stop all services...\n")
    except KeyboardInterrupt:
        pass
    finally:
        stop_all()