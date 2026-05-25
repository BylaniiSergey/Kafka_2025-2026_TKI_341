"""
Политики безопасности системы экзоскелета.

Каждая запись описывает РОВНО ОДНУ разрешённую операцию между
двумя модулями. Все обращения, не входящие в этот кортеж,
монитор безопасности обязан блокировать.

Формат:
    {"src": <модуль-источник>, "dst": <модуль-получатель>, "operation": <команда>}

Соответствие имён модулей — см. docs/Соответствие_компонентов.md
"""

policies = (
    # ============================================================
    # 1. КАНАЛ ВРАЧ ↔ ЭКЗОСКЕЛЕТ (зашифрованный)
    # ============================================================
    {"src": "doctor",          "dst": "crypto_encrypt",   "operation": "encrypt_command"},
    {"src": "crypto_encrypt",  "dst": "crypto_decrypt",   "operation": "transmit_cipher"},
    {"src": "crypto_decrypt",  "dst": "command_verify",   "operation": "verify_signature"},
    {"src": "command_verify",  "dst": "control_gateway",  "operation": "forward_command"},

    # Обратный канал — телеметрия пациенту/врачу
    {"src": "control_gateway", "dst": "patient_data",     "operation": "store_telemetry"},
    {"src": "patient_data",    "dst": "crypto_encrypt",   "operation": "encrypt_telemetry"},
    {"src": "crypto_encrypt",  "dst": "doctor",           "operation": "send_telemetry"},

    # ============================================================
    # 2. ШЛЮЗ → ИСПОЛНИТЕЛЬНЫЕ МОДУЛИ (нормальная работа)
    # ============================================================
    {"src": "control_gateway", "dst": "stop",             "operation": "smooth_stop"},
    {"src": "control_gateway", "dst": "stop",             "operation": "allow_movement"},
    {"src": "control_gateway", "dst": "stop",             "operation": "reset_emergency"},
    {"src": "control_gateway", "dst": "carriage",         "operation": "open"},
    {"src": "control_gateway", "dst": "carriage",         "operation": "close"},
    {"src": "control_gateway", "dst": "tactile",          "operation": "emit_feedback"},
    {"src": "control_gateway", "dst": "temperature",      "operation": "request_climate"},

    # ============================================================
    # 3. НЕЙРОННЫЕ И ТАКТИЛЬНЫЕ СИГНАЛЫ ОТ ПАЦИЕНТА
    # ============================================================
    {"src": "patient",         "dst": "neuro_verify",     "operation": "neural_signal"},
    {"src": "neuro_verify",    "dst": "control_gateway",  "operation": "verified_neural"},
    {"src": "patient",         "dst": "tactile",          "operation": "tactile_input"},
    {"src": "tactile",         "dst": "control_gateway",  "operation": "tactile_response"},

    # ============================================================
    # 4. АВАРИЙНАЯ ЦЕПОЧКА (приоритет над всем)
    # ============================================================
    # Пациент / врач инициируют аварийную остановку
    {"src": "patient",         "dst": "control_gateway",  "operation": "emergency_stop"},
    {"src": "doctor",          "dst": "control_gateway",  "operation": "emergency_stop"},

    # Аппаратные источники прямого аварийного сигнала
    {"src": "critical_battery","dst": "control_gateway",  "operation": "emergency_stop"},
    {"src": "critical_detect", "dst": "control_gateway",  "operation": "emergency_stop"},
    {"src": "sensor_verify",   "dst": "control_gateway",  "operation": "emergency_stop"},
    {"src": "position_verify", "dst": "control_gateway",  "operation": "emergency_stop"},
    {"src": "leg_force_control","dst": "control_gateway", "operation": "emergency_stop"},

    # Шлюз → модуль аварийной остановки
    {"src": "control_gateway", "dst": "stop",             "operation": "emergency_stop"},

    # Модуль остановки → аварийное открытие кабины
    {"src": "stop",            "dst": "carriage",         "operation": "emergency_open"},

    # ============================================================
    # 5. ТЕРМОРЕГУЛЯЦИЯ (датчик → шлюз → исполнительные)
    # ============================================================
    {"src": "temperature",     "dst": "control_gateway",  "operation": "climate_decision"},
    {"src": "control_gateway", "dst": "heating",          "operation": "set_level"},
    {"src": "control_gateway", "dst": "heating",          "operation": "off"},
    {"src": "control_gateway", "dst": "cooling",          "operation": "set_speed"},
    {"src": "control_gateway", "dst": "cooling",          "operation": "off"},

    # ============================================================
    # 6. ДАТЧИКИ И ВАЛИДАЦИЯ
    # ============================================================
    {"src": "critical_sensors",      "dst": "sensor_verify",     "operation": "report_data"},
    {"src": "critical_hand_sensors", "dst": "leg_force_control", "operation": "report_force"},

    # ============================================================
    # 7. НАВИГАЦИЯ
    # ============================================================
    {"src": "ins_nav",         "dst": "position_verify",  "operation": "ins_data"},
    {"src": "gnss_nav",        "dst": "position_verify",  "operation": "gnss_data"},
    {"src": "position_verify", "dst": "control_gateway",  "operation": "zone_status"},

    # ============================================================
    # 8. ДАННЫЕ ПАЦИЕНТА (хранилище)
    # ============================================================
    {"src": "control_gateway", "dst": "patient_data",     "operation": "save_state"},
    {"src": "patient_data",    "dst": "control_gateway",  "operation": "load_state"},
)


def check_operation(event_id, details) -> bool:
    """Проверка допустимости события согласно политикам безопасности.

    Возвращает True, если тройка (src, dst, operation) явно присутствует
    в кортеже policies. Любое отсутствие — отказ.
    """
    src: str = details.get("source")
    dst: str = details.get("deliver_to")
    op:  str = details.get("operation")

    if not all((src, dst, op)):
        print(f"[error] event {event_id}: missing src/dst/operation in {details}")
        return False

    print(f"[info] checking policies for event {event_id}, "
          f"{src} -> {dst} (operation: {op})")

    return {"src": src, "dst": dst, "operation": op} in policies
