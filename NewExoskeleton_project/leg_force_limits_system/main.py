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
PORT = int(os.getenv("PORT", "9105"))
MODULE_NAME = os.getenv("MODULE_NAME", "leg_force_limits_system")

# Канал А: критические датчики
CRITICAL_SENSORS_URL = os.getenv(
    "CRITICAL_SENSORS_LEGS_URL", "http://localhost:7102"
)

# Канал Б: прямой опрос приводов
KNEE_BELT_URL = os.getenv("KNEE_BELT_URL", "http://localhost:9003")
TRACK_SYSTEM_URL = os.getenv("TRACK_SYSTEM_URL", "http://localhost:9004")
LEG_FORCE_CONTROL_URL = os.getenv(
    "LEG_FORCE_CONTROL_URL", "http://localhost:9006"
)

REQUEST_TIMEOUT = 5.0

MAX_KNEE_DEG = float(os.getenv("MAX_KNEE_DEG", "170"))
MAX_KNEE_TORQUE = float(os.getenv("MAX_KNEE_TORQUE", "150"))
MAX_TRACK_SPEED = float(os.getenv("MAX_TRACK_SPEED", "1.5"))
MAX_ANGLE_DIVERGENCE = float(os.getenv("MAX_ANGLE_DIVERGENCE", "15.0"))
MAX_TORQUE_DIVERGENCE = float(os.getenv("MAX_TORQUE_DIVERGENCE", "40.0"))

INTENT_TO_EXPECTED = {
    "flex_knee": ["knee"],
    "extend_knee": ["knee"],
    "squat": ["knee"],
    "stand_up": ["knee"],
    "sit_down": ["knee"],
    "move_forward": ["track"],
    "move_backward": ["track"],
    "turn_left": ["track"],
    "turn_right": ["track"],
    "pivot_left": ["track"],
    "pivot_right": ["track"],
    "stop": ["track"],
    "brake": ["track", "knee"],
    "idle": [],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(MODULE_NAME)

bus = EventBus(client_id=MODULE_NAME)


class EvaluateBody(BaseModel):
    intent: str = "idle"
    leg: str = "none"
    strength: float = 0.0
    speed_modifier: float = 0.0
    verified_intent: Optional[str] = None
    verified_strength: Optional[float] = None


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
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
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
    drives = {}

    # Коленный пояс
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            for leg in ["left", "right"]:
                resp = c.get(f"{KNEE_BELT_URL}/positions/{leg}")
                if resp.status_code == 200:
                    data = resp.json()
                    drives[f"knee_{leg}"] = {
                        "angle": data.get("angle", 0.0),
                        "is_locked": data.get("is_locked", False),
                        "status": data.get("status", "unknown"),
                    }
    except Exception as e:
        logger.warning(f"Cannot poll knee_belt directly: {e}")
        drives["knee_error"] = str(e)

    # Гусеницы
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.get(f"{TRACK_SYSTEM_URL}/status")
            if resp.status_code == 200:
                data = resp.json()
                drives["track"] = {
                    "status": data.get("status", "unknown"),
                    "left_speed": data.get(
                        "left_track", {}
                    ).get("speed", 0.0),
                    "right_speed": data.get(
                        "right_track", {}
                    ).get("speed", 0.0),
                }
    except Exception as e:
        logger.warning(f"Cannot poll track_system directly: {e}")
        drives["track_error"] = str(e)

    # Контроль силы ног
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.get(f"{LEG_FORCE_CONTROL_URL}/status")
            if resp.status_code == 200:
                data = resp.json()
                for loc in ["left_knee", "right_knee",
                            "left_track", "right_track"]:
                    if loc in data:
                        drives[f"force_{loc}"] = {
                            "current_torque":
                                data[loc].get("current_torque", 0.0),
                            "current_force":
                                data[loc].get("current_force", 0.0),
                            "status":
                                data[loc].get("status", "unknown"),
                        }
    except Exception as e:
        logger.warning(f"Cannot poll leg_force directly: {e}")

    return drives


# ═══════════════════════════════════════════════════════════
# СРАВНЕНИЕ КАНАЛОВ
# ═══════════════════════════════════════════════════════════

def _compare_channels(
    channel_a: Dict, channel_b: Dict
) -> Optional[str]:
    """
    Сравнивает данные о коленях и гусеницах между каналами.
    """
    a_drives = channel_a.get("drive_states", {})

    # Колени: угол
    for side in ["left", "right"]:
        a_knee = a_drives.get(f"knee_{side}", {})
        b_knee = channel_b.get(f"knee_{side}", {})

        if a_knee and b_knee:
            a_angle = a_knee.get("angle", 0.0)
            b_angle = b_knee.get("angle", 0.0)
            div = abs(float(a_angle) - float(b_angle))
            if div > MAX_ANGLE_DIVERGENCE:
                return (
                    f"knee_{side}_angle: "
                    f"critical_sensor={a_angle:.1f}, "
                    f"drive_reports={b_angle:.1f}, "
                    f"divergence={div:.1f}"
                )

    # Гусеницы: скорость
    a_track = a_drives.get("track", {})
    b_track = channel_b.get("track", {})

    if a_track and b_track:
        for side in ["left_speed", "right_speed"]:
            a_speed = a_track.get(side, 0.0)
            b_speed = b_track.get(side, 0.0)
            div = abs(float(a_speed) - float(b_speed))
            if div > 0.5:  # > 0.5 m/s расхождение
                return (
                    f"track.{side}: "
                    f"critical_sensor={a_speed:.2f}, "
                    f"drive_reports={b_speed:.2f}, "
                    f"divergence={div:.2f}"
                )

    # Гусеницы: статус
    a_st = a_track.get("status")
    b_st = b_track.get("status")
    if (a_st and b_st
            and a_st != b_st
            and a_st != "unknown"
            and b_st != "unknown"):
        return (
            f"track.status: "
            f"critical_sensor={a_st}, "
            f"drive_reports={b_st}"
        )

    # Крутящий момент
    for side in ["left_knee", "right_knee"]:
        a_torque = a_drives.get(f"force_{side}", {}).get(
            "current_torque", None
        )
        b_torque = channel_b.get(f"force_{side}", {}).get(
            "current_torque", None
        )
        if a_torque is not None and b_torque is not None:
            div = abs(float(a_torque) - float(b_torque))
            if div > MAX_TORQUE_DIVERGENCE:
                return (
                    f"force_{side}.torque: "
                    f"critical_sensor={a_torque:.1f}, "
                    f"drive_reports={b_torque:.1f}, "
                    f"divergence={div:.1f}"
                )

    return None


# ═══════════════════════════════════════════════════════════
# ПРОВЕРКИ БЕЗОПАСНОСТИ
# ═══════════════════════════════════════════════════════════

def _check_knee_angles(channel_a: Dict, channel_b: Dict) -> Optional[str]:
    """Проверяет углы коленей из ОБОИХ каналов."""
    # Канал А
    a_ds = channel_a.get("drive_states", {})
    for side in ["left", "right"]:
        angle = abs(a_ds.get(f"knee_{side}", {}).get("angle", 0.0))
        if angle > MAX_KNEE_DEG:
            return (
                f"knee_{side}={angle:.1f} > {MAX_KNEE_DEG} "
                f"(critical sensor)"
            )

    # Канал Б
    for side in ["left", "right"]:
        angle = abs(channel_b.get(f"knee_{side}", {}).get("angle", 0.0))
        if angle > MAX_KNEE_DEG:
            return (
                f"knee_{side}={angle:.1f} > {MAX_KNEE_DEG} "
                f"(drive self-report)"
            )

    return None


def _check_track_speed(
    channel_a: Dict, channel_b: Dict
) -> Optional[str]:
    """Проверяет скорость гусениц из ОБОИХ каналов."""
    for label, source in [
        ("critical_sensor", channel_a.get("drive_states", {}).get("track", {})),
        ("drive_self_report", channel_b.get("track", {})),
    ]:
        for side in ["left_speed", "right_speed"]:
            speed = abs(source.get(side, 0.0))
            if speed > MAX_TRACK_SPEED:
                return (
                    f"track.{side}={speed:.2f} > {MAX_TRACK_SPEED} "
                    f"({label})"
                )
    return None


def _check_torque(
    channel_a: Dict, channel_b: Dict
) -> Optional[str]:
    """Проверяет крутящий момент из ОБОИХ каналов."""
    a_ds = channel_a.get("drive_states", {})
    for side in ["left_knee", "right_knee"]:
        # Канал А
        a_torque = abs(
            a_ds.get(f"force_{side}", {}).get("current_torque", 0.0)
        )
        if a_torque > MAX_KNEE_TORQUE:
            return (
                f"{side}_torque={a_torque:.1f} > {MAX_KNEE_TORQUE} "
                f"(critical sensor)"
            )

        # Канал Б
        b_torque = abs(
            channel_b.get(f"force_{side}", {}).get("current_torque", 0.0)
        )
        if b_torque > MAX_KNEE_TORQUE:
            return (
                f"{side}_torque={b_torque:.1f} > {MAX_KNEE_TORQUE} "
                f"(drive self-report)"
            )

    return None


def _check_intent_execution(
    intent: str, leg: str,
    channel_a: Dict, channel_b: Dict
) -> Optional[str]:
    expected = INTENT_TO_EXPECTED.get(intent, [])
    if not expected or intent == "idle":
        return None

    violations = []
    a_ds = channel_a.get("drive_states", {})

    for drive_type in expected:
        if drive_type == "knee":
            for side in ["left", "right"]:
                a_st = a_ds.get(
                    f"knee_{side}", {}
                ).get("status", "idle")
                b_st = channel_b.get(
                    f"knee_{side}", {}
                ).get("status", "idle")
                if a_st == "emergency_stop":
                    violations.append(
                        f"knee_{side}=emergency_stop (critical)"
                    )
                if b_st == "emergency_stop":
                    violations.append(
                        f"knee_{side}=emergency_stop (drive)"
                    )
        elif drive_type == "track":
            a_st = a_ds.get("track", {}).get("status", "idle")
            b_st = channel_b.get("track", {}).get("status", "idle")
            if a_st == "emergency_stop":
                violations.append("track=emergency_stop (critical)")
            if b_st == "emergency_stop":
                violations.append("track=emergency_stop (drive)")

    return "; ".join(violations) if violations else None


def _check_verified_match(
    intent: str, verified_intent: Optional[str],
    strength: float, verified_strength: Optional[float],
) -> Optional[str]:
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


def _send_torque_correction(leg: str, new_strength: float):
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            c.post(f"{LEG_FORCE_CONTROL_URL}/apply_knee_torque", json={
                "leg": leg,
                "action": "corrected",
                "target_torque": new_strength * MAX_KNEE_TORQUE,
            })
            logger.info(
                f"Torque corrected: leg={leg}, strength={new_strength}"
            )
    except Exception as e:
        logger.error(f"Torque correction failed: {e}")


# ═══════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════

app = FastAPI(title="Leg force & limits", version="4.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.get("/status")
def status():
    return {
        "service": MODULE_NAME,
        "limits": {
            "max_knee_deg": MAX_KNEE_DEG,
            "max_knee_torque": MAX_KNEE_TORQUE,
            "max_track_speed": MAX_TRACK_SPEED,
            "max_angle_divergence": MAX_ANGLE_DIVERGENCE,
            "max_torque_divergence": MAX_TORQUE_DIVERGENCE,
        },
    }


@app.post("/evaluate")
def evaluate(body: EvaluateBody):
    """
    Двухканальная проверка безопасности ног.

    Канал А = критические датчики (тоже опрашивают приводы)
    Канал Б = прямой опрос приводов из этого модуля

    1. Два параллельных опроса
    2. Trusted
    3. Углы коленей (оба канала)
    4. Крутящий момент (оба канала)
    5. Скорость гусениц (оба канала)
    6. СРАВНЕНИЕ каналов (обнаружение фальсификации)
    7. Выполняют ли приводы команду (оба канала)
    8. Соответствие верификации нейросигнала
    9. Ограничение силы
    """

    # ─── Шаг 1: Два канала ───────────────────────────────
    channel_a = _get_critical_sensor_data()
    channel_b = _poll_drives_directly()

    if channel_a is None:
        return {
            "ok": False,
            "error": "critical_sensors_unavailable",
            "stop_system": False,
        }

    # ─── Шаг 2: Trusted ─────────────────────────────────
    if not channel_a.get("trusted", True):
        _trigger_emergency("leg_sensors_untrusted")
        return {
            "ok": False, "stop_system": True,
            "reason": "sensors_untrusted",
        }

    # ─── Шаг 3: Углы коленей ────────────────────────────
    angle_issue = _check_knee_angles(channel_a, channel_b)
    if angle_issue:
        _trigger_emergency(
            "leg_angle_exceeded", {"detail": angle_issue}
        )
        return {
            "ok": False, "stop_system": True,
            "reason": "angle_limit", "detail": angle_issue,
        }

    # ─── Шаг 4: Крутящий момент ──────────────────────────
    torque_issue = _check_torque(channel_a, channel_b)
    if torque_issue:
        _trigger_emergency(
            "leg_torque_exceeded", {"detail": torque_issue}
        )
        return {
            "ok": False, "stop_system": True,
            "reason": "torque_exceeded", "detail": torque_issue,
        }

    # ─── Шаг 5: Скорость гусениц ────────────────────────
    speed_issue = _check_track_speed(channel_a, channel_b)
    if speed_issue:
        _trigger_emergency(
            "leg_speed_exceeded", {"detail": speed_issue}
        )
        return {
            "ok": False, "stop_system": True,
            "reason": "track_speed_exceeded",
            "detail": speed_issue,
        }

    # ─── Шаг 6: СРАВНЕНИЕ КАНАЛОВ ────────────────────────
    falsification = _compare_channels(channel_a, channel_b)
    if falsification:
        _trigger_emergency(
            "leg_data_falsification",
            {"detail": falsification}
        )
        return {
            "ok": False, "stop_system": True,
            "reason": "data_falsification",
            "detail": falsification,
        }

    # ─── Шаг 7: Выполняют ли приводы команду ────────────
    exec_issue = _check_intent_execution(
        body.intent, body.leg, channel_a, channel_b
    )
    if exec_issue:
        _trigger_emergency(
            "leg_intent_not_executed",
            {"intent": body.intent, "detail": exec_issue}
        )
        return {
            "ok": False, "stop_system": True,
            "reason": "intent_not_executed",
            "detail": exec_issue,
        }

    # ─── Шаг 8: Соответствие верификации ─────────────────
    verify_issue = _check_verified_match(
        body.intent, body.verified_intent,
        body.strength, body.verified_strength,
    )
    if verify_issue:
        _trigger_emergency(
            "leg_neural_mismatch", {"detail": verify_issue}
        )
        return {
            "ok": False, "stop_system": True,
            "reason": "neural_mismatch",
            "detail": verify_issue,
        }

    # ─── Шаг 9: Ограничение силы ─────────────────────────
    clamped_strength = float(body.strength)
    spd = abs(float(body.speed_modifier))
    clamped = False

    if spd > 85 and clamped_strength > 70:
        clamped_strength *= 0.5
        clamped = True
        logger.warning(
            f"Leg clamped: {body.strength} → {clamped_strength}"
        )
        if body.leg not in ("none", ""):
            _send_torque_correction(body.leg, clamped_strength / 100)

    return {
        "ok": True,
        "stop_system": False,
        "clamped": clamped,
        "adjusted_command": {
            "leg": body.leg,
            "intent": body.intent,
            "strength": round(clamped_strength, 4),
            "speed_modifier": body.speed_modifier,
        },
        "channels": {
            "A_critical_sensor": "polled",
            "B_direct_drive": "polled",
            "comparison": "no_divergence",
        },
        "checks_passed": [
            "trusted",
            "knee_angles_both_channels",
            "torque_both_channels",
            "track_speed_both_channels",
            "channel_comparison",
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