# arm_force_limits_system/main.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from typing import Any, Dict, Optional

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from kafka_bus import EventBus, TOPIC_EMERGENCY

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "7106"))
MODULE_NAME = os.getenv("MODULE_NAME", "arm_force_limits_system")

# Критические датчики (канал А — независимый аппаратный)
CRITICAL_SENSORS_URL = os.getenv(
    "CRITICAL_SENSORS_ARMS_URL", "http://localhost:7101"
)

# Приводы напрямую (канал Б)
UPPER_ARM_URL     = os.getenv("UPPER_ARM_URL",     "http://localhost:8003")
MIDDLE_ARM_URL    = os.getenv("MIDDLE_ARM_URL",    "http://localhost:8004")
FINGERS_URL       = os.getenv("FINGERS_URL",       "http://localhost:8005")
FORCE_CONTROL_URL = os.getenv("FORCE_CONTROL_URL", "http://localhost:8006")

REQUEST_TIMEOUT = 5.0

MAX_SHOULDER_DEG    = float(os.getenv("MAX_SHOULDER_DEG",    "150"))
MAX_ELBOW_DEG       = float(os.getenv("MAX_ELBOW_DEG",       "150"))
MAX_GRIP_FORCE      = float(os.getenv("MAX_GRIP_FORCE",      "150"))
MAX_ANGLE_DIVERGENCE = float(os.getenv("MAX_ANGLE_DIVERGENCE", "15.0"))
MAX_FORCE_DIVERGENCE = float(os.getenv("MAX_FORCE_DIVERGENCE", "30.0"))

INTENT_TO_EXPECTED = {
    "lift_arm":    ["upper"],
    "lower_arm":   ["upper"],
    "extend_arm":  ["upper", "middle"],
    "retract_arm": ["upper", "middle"],
    "flex_elbow":  ["middle"],
    "extend_elbow": ["middle"],
    "grasp":       ["fingers"],
    "release":     ["fingers"],
    "idle":        [],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(MODULE_NAME)

bus = EventBus(client_id=MODULE_NAME)


class EvaluateBody(BaseModel):
    intent:            str   = "idle"
    arm:               str   = "none"
    strength:          float = 0.0
    speed_modifier:    float = 0.0
    verified_intent:   Optional[str]   = None
    verified_strength: Optional[float] = None


# ── HTTP-клиент (патчится в тестах) ──────────────────────────────────────────

def get_client() -> httpx.Client:
    """
    Фабрика HTTP-клиента.
    Патчится в тестах через patch.object(mod, 'get_client', ...).
    """
    return httpx.Client(timeout=REQUEST_TIMEOUT)


# ── Emergency ─────────────────────────────────────────────────────────────────

def _trigger_emergency(reason: str, details: Dict = None):
    payload = {"source": MODULE_NAME, "reason": reason}
    if details:
        payload.update(details)
    bus.publish(TOPIC_EMERGENCY, payload)
    logger.error(f"EMERGENCY: {reason} | {details}")


# ═══════════════════════════════════════════════════════════
# КАНАЛ А: Критические датчики
# ═══════════════════════════════════════════════════════════

def _get_critical_sensor_data() -> Optional[Dict]:
    """
    Получает данные от критических датчиков (канал А).
    Использует get_client() — патчится в тестах.
    """
    try:
        with get_client() as c:
            resp = c.get(f"{CRITICAL_SENSORS_URL}/snapshot")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error(f"Critical sensors (channel A) unavailable: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# КАНАЛ Б: Прямой опрос приводов
# ═══════════════════════════════════════════════════════════

def _poll_drives_directly() -> Dict[str, Any]:
    """
    Опрашивает приводы напрямую (канал Б).
    Использует get_client() — патчится в тестах.
    """
    drives = {}

    with get_client() as c:
        # Верхний отдел (плечо)
        for arm in ["left", "right"]:
            try:
                resp = c.get(f"{UPPER_ARM_URL}/positions/{arm}")
                if resp.status_code == 200:
                    data = resp.json()
                    drives[f"upper_{arm}"] = {
                        "positions": data.get("positions", {}),
                        "status":    data.get("status", "unknown"),
                    }
            except Exception as e:
                logger.warning(f"Cannot poll upper_arm/{arm}: {e}")
                drives[f"upper_{arm}_error"] = str(e)

        # Средний отдел (локоть)
        for arm in ["left", "right"]:
            try:
                resp = c.get(f"{MIDDLE_ARM_URL}/positions/{arm}")
                if resp.status_code == 200:
                    data = resp.json()
                    drives[f"middle_{arm}"] = {
                        "positions": data.get("positions", {}),
                        "status":    data.get("status", "unknown"),
                    }
            except Exception as e:
                logger.warning(f"Cannot poll middle_arm/{arm}: {e}")
                drives[f"middle_{arm}_error"] = str(e)

        # Пальцы
        try:
            resp = c.get(f"{FINGERS_URL}/status")
            if resp.status_code == 200:
                data = resp.json()
                for arm in ["left", "right"]:
                    if arm in data:
                        drives[f"fingers_{arm}"] = {
                            "grip_percentage": data[arm].get(
                                "grip_percentage", 0.0
                            ),
                            "grip_force": data[arm].get("grip_force", 0.0),
                            "status":     data[arm].get("status", "unknown"),
                        }
        except Exception as e:
            logger.warning(f"Cannot poll fingers: {e}")
            drives["fingers_error"] = str(e)

        # Контроль силы
        try:
            resp = c.get(f"{FORCE_CONTROL_URL}/status")
            if resp.status_code == 200:
                data = resp.json()
                for arm in ["left", "right"]:
                    if arm in data:
                        drives[f"force_{arm}"] = {
                            "current_force": data[arm].get(
                                "current_force", 0.0
                            ),
                            "status": data[arm].get("status", "unknown"),
                        }
        except Exception as e:
            logger.warning(f"Cannot poll force_control: {e}")
            drives["force_error"] = str(e)

    return drives


# ═══════════════════════════════════════════════════════════
# СРАВНЕНИЕ КАНАЛОВ А и Б
# ═══════════════════════════════════════════════════════════

def _compare_channels(
    channel_a: Dict, channel_b: Dict, arm: str
) -> Optional[str]:
    """
    Сравнивает данные критических датчиков (канал А)
    с данными от самих приводов (канал Б).
    Возвращает описание расхождения или None.
    """
    a_drives = channel_a.get("drive_states", {})
    arm_key  = arm if arm in ("left", "right") else "right"

    # Плечо
    a_upper = a_drives.get(f"upper_{arm_key}", {})
    b_upper = channel_b.get(f"upper_{arm_key}", {})

    if a_upper and b_upper:
        a_positions = a_upper.get("positions", {})
        b_positions = b_upper.get("positions", {})
        for joint in [
            "shoulder_flexion",
            "shoulder_abduction",
            "shoulder_rotation",
        ]:
            a_val = a_positions.get(joint)
            b_val = b_positions.get(joint)
            if a_val is not None and b_val is not None:
                div = abs(float(a_val) - float(b_val))
                if div > MAX_ANGLE_DIVERGENCE:
                    return (
                        f"upper_{arm_key}.{joint}: "
                        f"critical_sensor={a_val:.1f}, "
                        f"drive_reports={b_val:.1f}, "
                        f"divergence={div:.1f}"
                    )

    # Локоть
    a_middle = a_drives.get(f"middle_{arm_key}", {})
    b_middle = channel_b.get(f"middle_{arm_key}", {})

    if a_middle and b_middle:
        a_pos = a_middle.get("positions", {})
        b_pos = b_middle.get("positions", {})
        for joint in ["elbow_flexion", "forearm_pronation"]:
            a_val = a_pos.get(joint)
            b_val = b_pos.get(joint)
            if a_val is not None and b_val is not None:
                div = abs(float(a_val) - float(b_val))
                if div > MAX_ANGLE_DIVERGENCE:
                    return (
                        f"middle_{arm_key}.{joint}: "
                        f"critical_sensor={a_val:.1f}, "
                        f"drive_reports={b_val:.1f}, "
                        f"divergence={div:.1f}"
                    )

    # Сила захвата
    a_fingers = a_drives.get(f"fingers_{arm_key}", {})
    b_fingers = channel_b.get(f"fingers_{arm_key}", {})

    if a_fingers and b_fingers:
        a_force = a_fingers.get("grip_force", 0.0)
        b_force = b_fingers.get("grip_force", 0.0)
        div = abs(float(a_force) - float(b_force))
        if div > MAX_FORCE_DIVERGENCE:
            return (
                f"fingers_{arm_key}.grip_force: "
                f"critical_sensor={a_force:.1f}, "
                f"drive_reports={b_force:.1f}, "
                f"divergence={div:.1f}"
            )

    # Контроллер силы
    a_force = a_drives.get(f"force_{arm_key}", {})
    b_force = channel_b.get(f"force_{arm_key}", {})

    if a_force and b_force:
        a_val = a_force.get("current_force", 0.0)
        b_val = b_force.get("current_force", 0.0)
        div = abs(float(a_val) - float(b_val))
        if div > MAX_FORCE_DIVERGENCE:
            return (
                f"force_{arm_key}: "
                f"critical_sensor={a_val:.1f}, "
                f"drive_reports={b_val:.1f}, "
                f"divergence={div:.1f}"
            )

    # Статусы
    a_status = a_drives.get(f"upper_{arm_key}", {}).get("status")
    b_status = channel_b.get(f"upper_{arm_key}", {}).get("status")
    if (
        a_status and b_status
        and a_status != b_status
        and a_status != "unknown"
        and b_status != "unknown"
    ):
        return (
            f"upper_{arm_key}.status: "
            f"critical_sensor={a_status}, "
            f"drive_reports={b_status}"
        )

    return None


# ═══════════════════════════════════════════════════════════
# ПРОВЕРКИ БЕЗОПАСНОСТИ
# ═══════════════════════════════════════════════════════════

def _check_biophysical_limits_from_critical(
    channel_a: Dict, arm: str
) -> Optional[str]:
    """Биофизические ограничения по данным критических датчиков (канал А)."""
    ds      = channel_a.get("drive_states", {})
    arm_key = arm if arm in ("left", "right") else "right"

    upper     = ds.get(f"upper_{arm_key}", {})
    positions = upper.get("positions", {})
    for joint, limit in [
        ("shoulder_flexion",   MAX_SHOULDER_DEG),
        ("shoulder_abduction", MAX_SHOULDER_DEG),
    ]:
        val = abs(positions.get(joint, 0.0))
        if val > limit:
            return f"{joint}={val:.1f} > {limit} (critical sensor)"

    middle = ds.get(f"middle_{arm_key}", {})
    m_pos  = middle.get("positions", {})
    elbow  = abs(m_pos.get("elbow_flexion", 0.0))
    if elbow > MAX_ELBOW_DEG:
        return (
            f"elbow_flexion={elbow:.1f} > {MAX_ELBOW_DEG} "
            "(critical sensor)"
        )

    return None


def _check_biophysical_limits_from_drives(
    channel_b: Dict, arm: str
) -> Optional[str]:
    """Биофизические ограничения по данным приводов (канал Б)."""
    arm_key   = arm if arm in ("left", "right") else "right"
    upper     = channel_b.get(f"upper_{arm_key}", {})
    positions = upper.get("positions", {})
    for joint, limit in [
        ("shoulder_flexion",   MAX_SHOULDER_DEG),
        ("shoulder_abduction", MAX_SHOULDER_DEG),
    ]:
        val = abs(positions.get(joint, 0.0))
        if val > limit:
            return f"{joint}={val:.1f} > {limit} (drive self-report)"

    middle = channel_b.get(f"middle_{arm_key}", {})
    m_pos  = middle.get("positions", {})
    elbow  = abs(m_pos.get("elbow_flexion", 0.0))
    if elbow > MAX_ELBOW_DEG:
        return (
            f"elbow_flexion={elbow:.1f} > {MAX_ELBOW_DEG} "
            "(drive self-report)"
        )

    return None


def _check_excessive_force(
    channel_a: Dict, channel_b: Dict, arm: str
) -> Optional[str]:
    """Проверяет силу из обоих каналов."""
    arm_key  = arm if arm in ("left", "right") else "right"
    a_drives = channel_a.get("drive_states", {})

    # Канал А
    a_fingers = a_drives.get(f"fingers_{arm_key}", {})
    a_grip    = a_fingers.get("grip_force", 0.0)
    if a_grip > MAX_GRIP_FORCE:
        return (
            f"grip_force={a_grip:.1f} > {MAX_GRIP_FORCE} "
            "(critical sensor)"
        )

    a_force   = a_drives.get(f"force_{arm_key}", {})
    a_current = a_force.get("current_force", 0.0)
    if a_current > MAX_GRIP_FORCE:
        return (
            f"current_force={a_current:.1f} > {MAX_GRIP_FORCE} "
            "(critical sensor)"
        )

    # Канал Б
    b_fingers = channel_b.get(f"fingers_{arm_key}", {})
    b_grip    = b_fingers.get("grip_force", 0.0)
    if b_grip > MAX_GRIP_FORCE:
        return (
            f"grip_force={b_grip:.1f} > {MAX_GRIP_FORCE} "
            "(drive self-report)"
        )

    b_force   = channel_b.get(f"force_{arm_key}", {})
    b_current = b_force.get("current_force", 0.0)
    if b_current > MAX_GRIP_FORCE:
        return (
            f"current_force={b_current:.1f} > {MAX_GRIP_FORCE} "
            "(drive self-report)"
        )

    return None


def _check_intent_execution(
    intent: str, arm: str,
    channel_a: Dict, channel_b: Dict,
) -> Optional[str]:
    """Проверяет выполнение команды приводами по обоим каналам."""
    expected = INTENT_TO_EXPECTED.get(intent, [])
    if not expected or intent == "idle":
        return None

    arm_key    = arm if arm in ("left", "right") else "right"
    violations = []

    for drive_type in expected:
        key = f"{drive_type}_{arm_key}"

        a_ds     = channel_a.get("drive_states", {})
        a_status = a_ds.get(key, {}).get("status", "idle")
        if a_status == "emergency_stop":
            violations.append(f"{key}=emergency_stop (critical sensor)")

        b_status = channel_b.get(key, {}).get("status", "idle")
        if b_status == "emergency_stop":
            violations.append(f"{key}=emergency_stop (drive self-report)")

    return "; ".join(violations) if violations else None


def _check_verified_match(
    intent:            str,
    verified_intent:   Optional[str],
    strength:          float,
    verified_strength: Optional[float],
) -> Optional[str]:
    """Проверяет соответствие верификации нейросигнала."""
    if verified_intent is None:
        return None
    if verified_intent != intent:
        return f"verified={verified_intent} != executing={intent}"
    if verified_strength is not None:
        diff = abs(strength - verified_strength)
        if diff > 0.4:
            return (
                f"strength: verified={verified_strength:.2f} "
                f"vs executing={strength:.2f}"
            )
    return None


def _send_force_correction(arm: str, new_strength: float):
    """Отправляет корректировку силы в force_control."""
    try:
        with get_client() as c:
            c.post(f"{FORCE_CONTROL_URL}/apply_force", json={
                "arm":        arm,
                "grip_type":  "corrected",
                "target_force": new_strength * 100,
                "max_force":  MAX_GRIP_FORCE,
            })
            logger.info(
                f"Force corrected: arm={arm}, strength={new_strength}"
            )
    except Exception as e:
        logger.error(f"Force correction failed: {e}")


# ═══════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════

app = FastAPI(title="Arm force & limits", version="4.1")


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.get("/status")
def status():
    return {
        "service": MODULE_NAME,
        "limits": {
            "max_shoulder_deg":    MAX_SHOULDER_DEG,
            "max_elbow_deg":       MAX_ELBOW_DEG,
            "max_grip_force":      MAX_GRIP_FORCE,
            "max_angle_divergence": MAX_ANGLE_DIVERGENCE,
            "max_force_divergence": MAX_FORCE_DIVERGENCE,
        },
    }


@app.post("/evaluate")
def evaluate(body: EvaluateBody):
    """
    Полная двухканальная проверка безопасности рук.

    Шаги:
    1. Получить данные по обоим каналам
    2. Trusted (канал А)
    3. Биофизика по каналу А
    4. Биофизика по каналу Б
    5. Сравнение каналов (обнаружение фальсификации)
    6. Чрезмерная сила (оба канала)
    7. Выполняют ли приводы команду (оба канала)
    8. Соответствие верификации нейросигнала
    9. Ограничение силы при необходимости
    """

    # Шаг 1: Два параллельных канала
    channel_a = _get_critical_sensor_data()
    channel_b = _poll_drives_directly()

    if channel_a is None:
        return {
            "ok":          False,
            "error":       "critical_sensors_unavailable",
            "stop_system": False,
        }

    # Шаг 2: Trusted
    if not channel_a.get("trusted", True):
        _trigger_emergency("arm_sensors_untrusted")
        return {
            "ok":          False,
            "stop_system": True,
            "reason":      "sensors_untrusted",
        }

    # Шаг 3: Биофизика по каналу А
    angle_a = _check_biophysical_limits_from_critical(channel_a, body.arm)
    if angle_a:
        _trigger_emergency(
            "arm_angle_exceeded_critical",
            {"detail": angle_a, "source": "critical_sensor"}
        )
        return {
            "ok":          False,
            "stop_system": True,
            "reason":      "angle_limit",
            "detail":      angle_a,
            "source":      "critical_sensor",
        }

    # Шаг 4: Биофизика по каналу Б
    angle_b = _check_biophysical_limits_from_drives(channel_b, body.arm)
    if angle_b:
        _trigger_emergency(
            "arm_angle_exceeded_drive",
            {"detail": angle_b, "source": "drive_self_report"}
        )
        return {
            "ok":          False,
            "stop_system": True,
            "reason":      "angle_limit",
            "detail":      angle_b,
            "source":      "drive_self_report",
        }

    # Шаг 5: Сравнение каналов (фальсификация)
    falsification = _compare_channels(channel_a, channel_b, body.arm)
    if falsification:
        _trigger_emergency(
            "arm_data_falsification",
            {"detail": falsification}
        )
        return {
            "ok":          False,
            "stop_system": True,
            "reason":      "data_falsification",
            "detail":      falsification,
        }

    # Шаг 6: Чрезмерная сила
    force_issue = _check_excessive_force(channel_a, channel_b, body.arm)
    if force_issue:
        _trigger_emergency(
            "arm_force_exceeded",
            {"detail": force_issue}
        )
        return {
            "ok":          False,
            "stop_system": True,
            "reason":      "force_exceeded",
            "detail":      force_issue,
        }

    # Шаг 7: Выполняют ли приводы команду
    exec_issue = _check_intent_execution(
        body.intent, body.arm, channel_a, channel_b
    )
    if exec_issue:
        _trigger_emergency(
            "arm_intent_not_executed",
            {"intent": body.intent, "detail": exec_issue}
        )
        return {
            "ok":          False,
            "stop_system": True,
            "reason":      "intent_not_executed",
            "detail":      exec_issue,
        }

    # Шаг 8: Соответствие верификации
    verify_issue = _check_verified_match(
        body.intent,          body.verified_intent,
        body.strength,        body.verified_strength,
    )
    if verify_issue:
        _trigger_emergency(
            "arm_neural_mismatch",
            {"detail": verify_issue}
        )
        return {
            "ok":          False,
            "stop_system": True,
            "reason":      "neural_mismatch",
            "detail":      verify_issue,
        }

    # Шаг 9: Ограничение силы
    clamped_strength = float(body.strength)
    arm_key          = (
        body.arm if body.arm in ("left", "right") else "right"
    )

    a_ds      = channel_a.get("drive_states", {})
    a_force   = a_ds.get(f"force_{arm_key}", {})
    a_current = a_force.get("current_force", 0.0)

    b_force   = channel_b.get(f"force_{arm_key}", {})
    b_current = b_force.get("current_force", 0.0)

    real_force = max(a_current, b_current)
    clamped    = False

    if real_force > MAX_GRIP_FORCE * 0.65:
        clamped_strength *= 0.55
        clamped = True
        logger.info(
            f"Force clamped: {body.strength} → {clamped_strength} "
            f"(real_force={real_force:.1f}, "
            f"A={a_current:.1f}, B={b_current:.1f})"
        )
        if body.arm not in ("none", "both", ""):
            _send_force_correction(body.arm, clamped_strength)

    return {
        "ok":          True,
        "stop_system": False,
        "clamped":     clamped,
        "adjusted_command": {
            "arm":           body.arm,
            "intent":        body.intent,
            "strength":      round(clamped_strength, 4),
            "speed_modifier": body.speed_modifier,
        },
        "channels": {
            "A_critical_sensor": "polled",
            "B_direct_drive":    "polled",
            "comparison":        "no_divergence",
        },
        "checks_passed": [
            "trusted",
            "angles_critical_sensor",
            "angles_drive_report",
            "channel_comparison",
            "force_both_channels",
            "intent_execution",
            "neural_verification",
        ],
    }


@app.post("/reset")
def reset():
    return {"ok": True}


if __name__ == "__main__":
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)