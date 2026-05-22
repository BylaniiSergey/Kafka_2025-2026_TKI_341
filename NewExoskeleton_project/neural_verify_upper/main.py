import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from typing import Any, Dict, Optional

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "7103"))
MODULE_NAME = os.getenv("MODULE_NAME", "neural_verify_upper")

ARM_MOVEMENT_URL = os.getenv("ARM_MOVEMENT_URL", "http://localhost:8002")
ARM_FORCE_LIMITS_URL = os.getenv(
    "ARM_FORCE_LIMITS_URL", "http://localhost:7106"
)
REQUEST_TIMEOUT = 5.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(MODULE_NAME)

_registered_patient_id: Optional[str] = None
_last: Optional[Dict[str, Any]] = None

MAX_STRENGTH_JUMP = 55.0
POSTURE_BLOCKED = {
    ("sitting", "lift_arm"),
    ("sitting", "extend_arm"),
}


class SessionInit(BaseModel):
    patient_id: str


class VerifyRequest(BaseModel):
    patient_id: str
    intent: str = "idle"
    target: str = "none"
    strength: float = 0.0
    speed_modifier: float = 1.0
    posture: str = "standing"


class AnalyzeInput(BaseModel):
    """Входные данные от neural_signal_system."""
    target_arm: str = "none"
    intent: str = "idle"
    strength: float = 0.0
    speed_modifier: float = 1.0
    can_execute: bool = False
    patient_id: str = ""
    posture: str = "standing"


app = FastAPI(title="Neural verify — upper limbs", version="2.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.post("/session/init")
def session_init(body: SessionInit):
    global _registered_patient_id, _last
    _registered_patient_id = body.patient_id
    _last = None
    logger.info(f"Session init: {body.patient_id}")
    return {"ok": True, "patient_id": _registered_patient_id}


@app.post("/verify")
def verify(body: VerifyRequest):
    """Прямая верификация одной команды (для тестов)."""
    global _last

    if not _registered_patient_id:
        return {"allowed": False, "reason": "session_not_initialized"}

    if body.patient_id != _registered_patient_id:
        logger.warning(
            f"Patient ID mismatch: "
            f"expected={_registered_patient_id}, got={body.patient_id}"
        )
        return {"allowed": False, "reason": "patient_id_mismatch"}

    intent = body.intent.lower()
    posture = body.posture.lower()

    if (posture, intent) in POSTURE_BLOCKED:
        logger.warning(f"Intent '{intent}' blocked for posture '{posture}'")
        return {
            "allowed": False,
            "reason": "intent_inconsistent_with_posture",
        }

    if _last is not None:
        ds = abs(float(body.strength) - float(_last.get("strength", 0)))
        if ds > MAX_STRENGTH_JUMP:
            logger.warning(f"Strength jump: {ds}")
            return {"allowed": False, "reason": "strength_jump_too_large"}

        prev_idle = _last.get("intent") == "idle"
        curr_active = intent not in ("idle", "release")
        if prev_idle and curr_active and float(body.strength) > 70:
            return {
                "allowed": False,
                "reason": "idle_to_high_power_transition",
            }

    _last = {
        "intent": intent,
        "target": body.target,
        "strength": body.strength,
    }

    return {"allowed": True, "reason": "ok"}


@app.post("/process")
def process_and_forward(body: AnalyzeInput):
    """
    Полная цепочка:
    1. Верифицировать нейросигнал
    2. Если ок — отправить в arm_force_limits для проверки силы
    3. Если force_limits ок — отправить в arm_movement_system
    4. Вернуть результат
    """
    global _last

    # Шаг 1: Верификация
    if not body.can_execute:
        return {
            "ok": True,
            "action": "no_execute",
            "reason": "signal_not_executable",
        }

    if _registered_patient_id and body.patient_id:
        if body.patient_id != _registered_patient_id:
            return {
                "ok": False,
                "action": "blocked",
                "reason": "patient_id_mismatch",
            }

    intent = body.intent.lower()
    posture = body.posture.lower()

    if (posture, intent) in POSTURE_BLOCKED:
        logger.warning(f"Blocked: {intent} while {posture}")
        return {
            "ok": False,
            "action": "blocked",
            "reason": "intent_inconsistent_with_posture",
        }

    if _last is not None:
        ds = abs(float(body.strength) - float(_last.get("strength", 0)))
        if ds > MAX_STRENGTH_JUMP:
            return {
                "ok": False,
                "action": "blocked",
                "reason": "strength_jump_too_large",
            }

        prev_idle = _last.get("intent") == "idle"
        curr_active = intent not in ("idle", "release")
        if prev_idle and curr_active and float(body.strength) > 70:
            return {
                "ok": False,
                "action": "blocked",
                "reason": "idle_to_high_power_transition",
            }

    _last = {
        "intent": intent,
        "target": body.target_arm,
        "strength": body.strength,
    }

    # Шаг 2: Отправить в arm_force_limits
    force_result = None
    adjusted_strength = body.strength
    adjusted_speed = body.speed_modifier

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(f"{ARM_FORCE_LIMITS_URL}/evaluate", json={
                "intent": body.intent,
                "arm": body.target_arm,
                "strength": body.strength,
                "speed_modifier": body.speed_modifier,
                "verified_intent": body.intent,
                "verified_strength": body.strength,
            })
            force_result = resp.json()

            if force_result.get("stop_system"):
                logger.error(
                    f"Force limits STOP: {force_result.get('reason')}"
                )
                return {
                    "ok": False,
                    "action": "emergency_stop",
                    "reason": force_result.get("reason"),
                    "force_result": force_result,
                }

            adjusted = force_result.get("adjusted_command", {})
            adjusted_strength = adjusted.get("strength", body.strength)
            adjusted_speed = adjusted.get(
                "speed_modifier", body.speed_modifier
            )
    except Exception as e:
        logger.error(f"Force limits unavailable: {e}")

    # Шаг 3: Отправить в arm_movement_system
    movement_result = None
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(f"{ARM_MOVEMENT_URL}/execute", json={
                "arm": body.target_arm,
                "intent": body.intent,
                "strength": adjusted_strength,
                "speed_modifier": adjusted_speed,
            })
            if resp.status_code in (200, 204):
                movement_result = (
                    resp.json() if resp.status_code == 200 else {"sent": True}
                )
            else:
                movement_result = {"error": f"HTTP {resp.status_code}"}
    except Exception as e:
        logger.error(f"Arm movement unavailable: {e}")
        movement_result = {"error": str(e)}

    logger.info(
        f"Processed: arm={body.target_arm}, intent={body.intent}, "
        f"strength={adjusted_strength}"
    )

    return {
        "ok": True,
        "action": "executed",
        "verified_intent": body.intent,
        "verified_target": body.target_arm,
        "adjusted_strength": adjusted_strength,
        "adjusted_speed": adjusted_speed,
        "force_result": force_result,
        "movement_result": movement_result,
    }


@app.post("/reset")
def reset():
    global _registered_patient_id, _last
    _registered_patient_id = None
    _last = None
    return {"ok": True}


if __name__ == "__main__":
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)