import os
from typing import Optional

MODULE_URLS = {
    "control_system":                   os.getenv("CONTROL_SYSTEM_URL",         "http://localhost:8000"),
    "neural_signal_system":             os.getenv("NEURAL_SIGNAL_URL",           "http://localhost:8001"),
    "arm_movement_system":              os.getenv("ARM_MOVEMENT_URL",            "http://localhost:8002"),
    "upper_arm_system":                 os.getenv("UPPER_ARM_URL",               "http://localhost:8003"),
    "middle_arm_system":                os.getenv("MIDDLE_ARM_URL",              "http://localhost:8004"),
    "fingers_system":                   os.getenv("FINGERS_URL",                 "http://localhost:8005"),
    "force_control_system":             os.getenv("FORCE_CONTROL_URL",           "http://localhost:8006"),
    "leg_neural_signal_system":         os.getenv("LEG_NEURAL_URL",              "http://localhost:9001"),
    "leg_movement_system":              os.getenv("LEG_MOVEMENT_URL",            "http://localhost:9002"),
    "knee_belt_system":                 os.getenv("KNEE_BELT_URL",               "http://localhost:9003"),
    "track_system":                     os.getenv("TRACK_SYSTEM_URL",            "http://localhost:9004"),
    "leg_force_control_system":         os.getenv("LEG_FORCE_URL",               "http://localhost:9006"),
    "stop_module":                      os.getenv("STOP_MODULE_URL",             "http://localhost:7001"),
    "carriage_system":                  os.getenv("CARRIAGE_URL",                "http://localhost:7002"),
    "temperature_system":               os.getenv("TEMPERATURE_URL",             "http://localhost:7003"),
    "heating_system":                   os.getenv("HEATING_URL",                 "http://localhost:7004"),
    "cooling_system":                   os.getenv("COOLING_URL",                 "http://localhost:7005"),
    "tactile_system":                   os.getenv("TACTILE_URL",                 "http://localhost:7006"),
    "comms_module":                     os.getenv("COMMS_URL",                   "http://localhost:6001"),
    "monitoring_system":                os.getenv("MONITORING_URL",              "http://localhost:6002"),
    "sensors_module":                   os.getenv("SENSORS_URL",                 "http://localhost:6003"),
    "battery_controller":               os.getenv("BATTERY_CTRL_URL",            "http://localhost:6004"),
    "charger_module":                   os.getenv("CHARGER_URL",                 "http://localhost:6005"),
    "battery_cell":                     os.getenv("BATTERY_CELL_URL",            "http://localhost:6006"),
    "crypto_module":                    os.getenv("CRYPTO_URL",                  "http://localhost:4001"),
    "critical_battery_monitor":         os.getenv("CRITICAL_BATTERY_URL",        "http://localhost:4002"),
    "critical_sensors":                 os.getenv("CRITICAL_SENSORS_URL",        "http://localhost:4003"),
    "critical_sensors_arms":            os.getenv("CRITICAL_SENSORS_ARMS_URL",   "http://localhost:7101"),
    "critical_sensors_legs":            os.getenv("CRITICAL_SENSORS_LEGS_URL",   "http://localhost:7102"),
    "neural_verify_upper":              os.getenv("NEURAL_VERIFY_UPPER_URL",     "http://localhost:7103"),
    "neural_verify_lower":              os.getenv("NEURAL_VERIFY_LOWER_URL",     "http://localhost:7104"),
    "temperature_measurement_system":   os.getenv("TEMPERATURE_MEASUREMENT_URL", "http://localhost:7105"),
    "arm_force_limits_system":          os.getenv("ARM_FORCE_LIMITS_URL",        "http://localhost:7106"),
    "leg_force_limits_system":          os.getenv("LEG_FORCE_LIMITS_URL",        "http://localhost:9105"),
    "task_orchestrator":                os.getenv("TASK_ORCHESTRATOR_URL",       "http://localhost:5000"),
    "emergency_control_module":         os.getenv("EMERGENCY_CONTROL_URL",       "http://localhost:5001"),
    "emergency_open_module":            os.getenv("EMERGENCY_OPEN_URL",          "http://localhost:5002"),
    "emergency_stop_module":            os.getenv("EMERGENCY_STOP_URL",          "http://localhost:5003"),
    "tactile_verification_module":      os.getenv("TACTILE_VERIFY_URL",          "http://localhost:5004"),
    "position_check_module":            os.getenv("POSITION_CHECK_URL",          "http://localhost:5005"),
    "gnss_navigation_module":           os.getenv("GNSS_URL",                    "http://localhost:5006"),
    "ins_navigation_module":            os.getenv("INS_URL",                     "http://localhost:5007"),
    "command_verification":             os.getenv("COMMAND_VERIFY_URL",          "http://localhost:5101"),
    "critical_situation_recognition":   os.getenv("CRITICAL_SITUATION_URL",      "http://localhost:5102"),
    "sensor_verification":              os.getenv("SENSOR_VERIFICATION_URL",     "http://localhost:5104"),
    "decryption_module":                os.getenv("DECRYPTION_URL",              "http://localhost:5103"),
}

LINK_POLICIES: dict[tuple[str, str], set[tuple[str, str]]] = {

    # ── control_system ───────────────────────────────────────

    ("control_system", "neural_signal_system"): {
        ("/analyze", "POST"),
        ("/health",  "GET"),
        ("/reset",   "POST"),
    },

    ("control_system", "leg_neural_signal_system"): {
        ("/analyze", "POST"),
        ("/health",  "GET"),
        ("/reset",   "POST"),
    },

    ("control_system", "arm_movement_system"): {
        ("/execute",         "POST"),
        ("/emergency_stop",  "POST"),
        ("/reset",           "POST"),
        ("/status",          "GET"),
        ("/movement_history","GET"),
    },

    ("control_system", "leg_movement_system"): {
        ("/execute",         "POST"),
        ("/emergency_stop",  "POST"),
        ("/reset",           "POST"),
        ("/status",          "GET"),
        ("/movement_history","GET"),
    },

    ("control_system", "upper_arm_system"): {
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
        ("/status",         "GET"),
    },

    ("control_system", "middle_arm_system"): {
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
        ("/status",         "GET"),
    },

    ("control_system", "fingers_system"): {
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
        ("/status",         "GET"),
    },

    ("control_system", "force_control_system"): {
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
        ("/status",         "GET"),
    },

    ("control_system", "knee_belt_system"): {
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
        ("/status",         "GET"),
    },

    ("control_system", "track_system"): {
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
        ("/status",         "GET"),
    },

    ("control_system", "leg_force_control_system"): {
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
        ("/status",         "GET"),
    },

    ("control_system", "stop_module"): {
        ("/emergency-stop",  "POST"),
        ("/smooth-stop",     "POST"),
        ("/allow-movement",  "POST"),
        ("/reset-emergency", "POST"),
        ("/status",          "GET"),
    },

    ("control_system", "carriage_system"): {
        ("/open",   "POST"),
        ("/close",  "POST"),
        ("/status", "GET"),
    },

    ("control_system", "temperature_system"): {
        ("/sensors", "POST"),
        ("/decide",  "POST"),
        ("/status",  "GET"),
    },

    ("control_system", "heating_system"): {
        ("/level",  "POST"),
        ("/off",    "POST"),
        ("/status", "GET"),
    },

    ("control_system", "cooling_system"): {
        ("/speed",  "POST"),
        ("/off",    "POST"),
        ("/status", "GET"),
    },

    ("control_system", "tactile_system"): {
        ("/emit",   "POST"),
        ("/status", "GET"),
    },

    ("control_system", "comms_module"): {
        ("/alarm",               "POST"),
        ("/alarm_encrypted",     "POST"),
        ("/telemetry_encrypted", "POST"),
        ("/command_encrypted",   "POST"),
        ("/status",              "GET"),
        ("/comms_history",       "GET"),
    },

    ("control_system", "monitoring_system"): {
        ("/telemetry",         "GET"),
        ("/emergency_stop",    "POST"),
        ("/status",            "GET"),
    },

    ("control_system", "sensors_module"): {
        ("/readings",      "GET"),
        ("/set_max_torque","POST"),
    },

    ("control_system", "battery_controller"): {
        ("/status",          "GET"),
        ("/control/charge",  "POST"),
        ("/discharge",       "POST"),
    },

    ("control_system", "charger_module"): {
        ("/status",  "GET"),
        ("/control", "POST"),
        ("/plug",    "POST"),
    },

    ("control_system", "battery_cell"): {
        ("/status",    "GET"),
        ("/discharge", "POST"),
        ("/charge",    "POST"),
    },

    ("control_system", "crypto_module"): {
        ("/encrypt", "POST"),
        ("/health",  "GET"),
    },

    # ── Верхние конечности ────────────────────────────────────

    ("neural_signal_system", "neural_verify_upper"): {
        ("/process", "POST"),
        ("/health",  "GET"),
    },

    ("neural_verify_upper", "arm_force_limits_system"): {
        ("/evaluate", "POST"),
        ("/health",   "GET"),
    },

    ("neural_verify_upper", "arm_movement_system"): {
        ("/execute", "POST"),
    },

    ("arm_movement_system", "upper_arm_system"): {
        ("/move",           "POST"),
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
    },

    ("arm_movement_system", "middle_arm_system"): {
        ("/move",           "POST"),
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
    },

    ("arm_movement_system", "fingers_system"): {
        ("/move",           "POST"),
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
    },

    ("fingers_system", "force_control_system"): {
        ("/apply_force",    "POST"),
        ("/release",        "POST"),
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
    },

    ("arm_force_limits_system", "critical_sensors_arms"): {
        ("/snapshot", "GET"),
        ("/health",   "GET"),
    },

    ("arm_force_limits_system", "upper_arm_system"): {
        ("/positions/*", "GET"),
        ("/status",      "GET"),
    },

    ("arm_force_limits_system", "middle_arm_system"): {
        ("/positions/*", "GET"),
        ("/status",      "GET"),
    },

    ("arm_force_limits_system", "fingers_system"): {
        ("/status", "GET"),
    },

    ("arm_force_limits_system", "force_control_system"): {
        ("/status",       "GET"),
        ("/apply_force",  "POST"),
    },

    ("critical_sensors_arms", "upper_arm_system"): {
        ("/positions/*", "GET"),
    },

    ("critical_sensors_arms", "middle_arm_system"): {
        ("/positions/*", "GET"),
    },

    ("critical_sensors_arms", "fingers_system"): {
        ("/status", "GET"),
    },

    ("critical_sensors_arms", "force_control_system"): {
        ("/status", "GET"),
    },

    # ── Нижние конечности ─────────────────────────────────────

    ("leg_neural_signal_system", "neural_verify_lower"): {
        ("/process", "POST"),
        ("/health",  "GET"),
    },

    ("neural_verify_lower", "leg_force_limits_system"): {
        ("/evaluate", "POST"),
        ("/health",   "GET"),
    },

    ("neural_verify_lower", "leg_movement_system"): {
        ("/execute", "POST"),
    },

    ("leg_movement_system", "knee_belt_system"): {
        ("/move",           "POST"),
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
    },

    ("leg_movement_system", "track_system"): {
        ("/move",           "POST"),
        ("/emergency_stop", "POST"),
        ("/reset",          "POST"),
    },

    ("leg_movement_system", "leg_force_control_system"): {
        ("/apply_knee_torque",  "POST"),
        ("/apply_track_force",  "POST"),
        ("/release",            "POST"),
        ("/emergency_stop",     "POST"),
        ("/reset",              "POST"),
    },

    ("leg_force_limits_system", "critical_sensors_legs"): {
        ("/snapshot", "GET"),
        ("/health",   "GET"),
    },

    ("leg_force_limits_system", "knee_belt_system"): {
        ("/positions/*", "GET"),
        ("/status",      "GET"),
    },

    ("leg_force_limits_system", "track_system"): {
        ("/status", "GET"),
    },

    ("leg_force_limits_system", "leg_force_control_system"): {
        ("/status",            "GET"),
        ("/apply_knee_torque", "POST"),
    },

    ("critical_sensors_legs", "knee_belt_system"): {
        ("/positions/*", "GET"),
    },

    ("critical_sensors_legs", "track_system"): {
        ("/status", "GET"),
    },

    ("critical_sensors_legs", "leg_force_control_system"): {
        ("/status", "GET"),
    },

    # ── Позиционирование ──────────────────────────────────────

    ("gnss_navigation_module", "position_check_module"): {
        ("/gnss_update", "POST"),
    },

    ("ins_navigation_module", "position_check_module"): {
        ("/ins_update", "POST"),
    },

    # ── Аварийная цепочка (HTTP) ──────────────────────────────

    ("emergency_control_module", "emergency_open_module"): {
        ("/open",   "POST"),
        ("/status", "GET"),
    },

    ("emergency_control_module", "emergency_stop_module"): {
        ("/safe_pose", "POST"),
        ("/status",    "GET"),
    },

    ("emergency_stop_module", "arm_movement_system"): {
        ("/emergency_stop", "POST"),
        ("/execute",        "POST"),
    },

    ("emergency_stop_module", "leg_movement_system"): {
        ("/emergency_stop", "POST"),
        ("/execute",        "POST"),
    },

    ("emergency_stop_module", "upper_arm_system"): {
        ("/emergency_stop", "POST"),
    },

    ("emergency_stop_module", "middle_arm_system"): {
        ("/emergency_stop", "POST"),
    },

    ("emergency_stop_module", "fingers_system"): {
        ("/emergency_stop", "POST"),
    },

    ("emergency_stop_module", "force_control_system"): {
        ("/emergency_stop", "POST"),
    },

    ("emergency_stop_module", "knee_belt_system"): {
        ("/emergency_stop", "POST"),
    },

    ("emergency_stop_module", "track_system"): {
        ("/emergency_stop", "POST"),
    },

    ("emergency_stop_module", "leg_force_control_system"): {
        ("/emergency_stop", "POST"),
    },

    # ── Мониторинг / связь / батарея ─────────────────────────

    ("monitoring_system", "sensors_module"): {
        ("/readings", "GET"),
    },

    ("monitoring_system", "battery_controller"): {
        ("/status", "GET"),
    },

    ("monitoring_system", "comms_module"): {
        ("/alarm", "POST"),
    },

    ("battery_controller", "charger_module"): {
        ("/status",  "GET"),
        ("/control", "POST"),
    },

    ("battery_controller", "battery_cell"): {
        ("/status",    "GET"),
        ("/discharge", "POST"),
        ("/charge",    "POST"),
    },

    ("comms_module", "monitoring_system"): {
        ("/emergency_stop", "POST"),
    },

    ("comms_module", "sensors_module"): {
        ("/set_max_torque", "POST"),
    },

    # ── Тактильная верификация ────────────────────────────────

    ("tactile_verification_module", "tactile_system"): {
        ("/emit",   "POST"),
        ("/status", "GET"),
    },

    # ── Крипто / расшифровка ──────────────────────────────────

    ("decryption_module", "crypto_module"): {
        ("/decrypt", "POST"),
        ("/health",  "GET"),
    },

    ("decryption_module", "comms_module"): {
        ("/latest_encrypted_packet", "GET"),
        ("/encrypted_packets",       "GET"),
    },

    # ── Верификация сенсоров ──────────────────────────────────

    ("sensor_verification", "sensors_module"): {
        ("/readings", "GET"),
    },

    ("sensor_verification", "critical_sensors"): {
        ("/readings", "GET"),
        ("/status",   "GET"),
    },
}


KAFKA_POLICIES: dict[tuple[str, str, str], set[str]] = {

    # ── exo.commands — управляющие команды ───────────────────

    ("task_orchestrator", "control_system", "exo.commands"): {
        "start_arms",
        "start_legs",
        "start_full",
        "arm_cycle",
        "leg_cycle",
        "full_cycle",
        "emergency_stop",
        "reset",
    },

    # ── exo.emergency — кто может объявить аварию ────────────

    ("arm_force_limits_system", "emergency_control_module", "exo.emergency"): {
        "arm_angle_exceeded_critical",
        "arm_angle_exceeded_drive",
        "arm_data_falsification",
        "arm_force_exceeded",
        "arm_intent_not_executed",
        "arm_neural_mismatch",
        "arm_sensors_untrusted",
    },

    ("leg_force_limits_system", "emergency_control_module", "exo.emergency"): {
        "leg_angle_exceeded",
        "leg_torque_exceeded",
        "leg_speed_exceeded",
        "leg_data_falsification",
        "leg_intent_not_executed",
        "leg_neural_mismatch",
        "leg_sensors_untrusted",
    },

    ("critical_battery_monitor", "emergency_control_module", "exo.emergency"): {
        "critical_battery",
        "critical_battery_test",
    },

    ("critical_situation_recognition", "emergency_control_module", "exo.emergency"): {
        "critical_joint_angle",
        "critical_joint_angular_velocity",
        "critical_torque",
        "critical_motor_temp",
        "critical_imu_acceleration",
        "critical_balance_deviation",
    },

    ("temperature_measurement_system", "emergency_control_module", "exo.emergency"): {
        "thermal_overheat",
        "hypothermia_risk",
        "sensor_untrusted",
    },

    ("position_check_module", "emergency_control_module", "exo.emergency"): {
        "position_out_of_zone",
        "ins_gnss_divergence",
    },

    # ── exo.sensors.raw — публикация сырых данных ────────────

    ("sensors_module", "sensor_verification", "exo.sensors.raw"): set(),

    # ── exo.sensors.verified — верифицированные данные ───────

    ("sensor_verification", "monitoring_system", "exo.sensors.verified"): set(),
    ("sensor_verification", "critical_situation_recognition", "exo.sensors.verified"): set(),

    # ── exo.alarms — алармы мониторинга ──────────────────────

    ("monitoring_system", "comms_module", "exo.alarms"): {
        "HYPEREXTENSION",
        "BATTERY_LOW",
        "MOTOR_OVERHEAT",
    },

    # ── exo.telemetry — телеметрия ────────────────────────────

    ("monitoring_system", "control_system", "exo.telemetry"): set(),

    # ── exo.link.requests — запросы через security monitor ───

    ("*", "security_link_monitor", "exo.link.requests"): set(),
}


# ─────────────────────────────────────────────────────────────
# Вспомогательные функции
# ─────────────────────────────────────────────────────────────

def resolve_target_url(module_name: str) -> Optional[str]:
    return MODULE_URLS.get(module_name)


def _check_source_token(source: str, token: str) -> bool:
    env_name = f"LINK_TOKEN_{source.upper()}"
    expected = os.getenv(env_name)
    if not expected:
        return True
    return token == expected


def _match_path(policy_path: str, request_path: str) -> bool:
    """
    Сравнивает путь запроса с шаблоном политики.
    Поддерживает суффиксный wildcard: /positions/*
    """
    if policy_path.endswith("/*"):
        prefix = policy_path[:-2]
        return request_path == prefix or request_path.startswith(prefix + "/")
    return policy_path == request_path


def _check_http(
    event_id: str,
    src: str,
    dst: str,
    path: str,
    method: str,
) -> tuple[bool, str]:
    allowed_ops = LINK_POLICIES.get((src, dst))
    if allowed_ops is None:
        return False, "http_link_not_allowed"

    method_upper = method.upper()

    for policy_path, policy_method in allowed_ops:
        path_ok   = _match_path(policy_path, path)
        method_ok = policy_method == "*" or policy_method == method_upper

        if path_ok and method_ok:
            return True, "ok"

    return False, f"http_operation_not_allowed:{method_upper}:{path}"


def _check_kafka(
    event_id: str,
    src: str,
    dst: str,
    topic: str,
    command: str,
) -> tuple[bool, str]:
    # Проверяем точное совпадение
    allowed_commands = KAFKA_POLICIES.get((src, dst, topic))

    # Проверяем wildcard источника ("*")
    if allowed_commands is None:
        allowed_commands = KAFKA_POLICIES.get(("*", dst, topic))

    if allowed_commands is None:
        return False, "kafka_link_not_allowed"

    # Пустое множество = разрешено любое сообщение
    if not allowed_commands:
        return True, "ok"

    if command in allowed_commands:
        return True, "ok"

    return False, f"kafka_command_not_allowed:{command}"


def check_operation(event_id: str, details: dict) -> tuple[bool, str]:
    src       = str(details.get("source",     "")).strip()
    dst       = str(details.get("deliver_to", "")).strip()
    transport = str(details.get("transport",  "kafka")).lower().strip()
    token     = str(details.get("token",      "")).strip()

    if not src or not dst:
        return False, "missing_source_or_destination"

    if not _check_source_token(src, token):
        return False, "invalid_source_token"

    if transport == "http":
        path   = str(details.get("path",   "/")).strip()
        method = str(details.get("method", "POST")).strip()
        return _check_http(event_id, src, dst, path, method)

    if transport == "kafka":
        topic   = str(details.get("topic",   "")).strip()
        command = str(details.get("command", "")).strip()

        if not topic:
            return False, "missing_kafka_topic"

        return _check_kafka(event_id, src, dst, topic, command)

    return False, f"unknown_transport:{transport}"