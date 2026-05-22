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
PORT = int(os.getenv("PORT", "7104"))
MODULE_NAME = os.getenv("MODULE_NAME", "neural_verify_lower")

LEG_MOVEMENT_URL = os.getenv("LEG_MOVEMENT_URL", "http://localhost:9002")
LEG_FORCE_LIMITS_URL = os.getenv(
    "LEG_FORCE_LIMITS_URL", "http://localhost:9105"
)
REQUEST_TIMEOUT = 5.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(MODULE_NAME)

_registered_patient_id: Optional[str] = None
_last: Optional[Dict[str, Any]] = None

MAX_STRENGTH_JUMP = 60.0
LOCOMOTION_INTENTS = {
    "step_forward", "step_back", "walk",
    "move_forward", "move_backward",
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
    target_leg: str = "none"
    intent: str = "idle"
    strength: float = 0.0
    speed_modifier: float = 1.0
    can_execute: bool = False
    patient_id: str = ""
    posture: str = "standing"


app = FastAPI(title="Neural verify — lower limbs", version="2.0")


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
    global _last

    if not _registered_patient_id:
        return {"allowed": False, "reason": "session_not_initialized"}

    if body.patient_id != _registered_patient_id:
        return {"allowed": False, "reason": "patient_id_mismatch"}

    intent = body.intent.lower()
    posture = body.posture.lower()

    if posture == "sitting" and intent in LOCOMOTION_INTENTS:
        return {"allowed": False, "reason": "locomotion_while_seated"}

    if _last is not None:
        ds = abs(float(body.strength) - float(_last.get("strength", 0)))
        if ds > MAX_STRENGTH_JUMP:
            return {"allowed": False, "reason": "strength_jump_too_large"}

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
    2. Отправить в leg_force_limits
    3. Отправить в leg_movement_system
    """
    global _last

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

    if posture == "sitting" and intent in LOCOMOTION_INTENTS:
        return {
            "ok": False,
            "action": "blocked",
            "reason": "locomotion_while_seated",
        }

    if _last is not None:
        ds = abs(float(body.strength) - float(_last.get("strength", 0)))
        if ds > MAX_STRENGTH_JUMP:
            return {
                "ok": False,
                "action": "blocked",
                "reason": "strength_jump_too_large",
            }

    _last = {
        "intent": intent,
        "target": body.target_leg,
        "strength": body.strength,
    }

    # Шаг 2: leg_force_limits
    force_result = None
    adjusted_strength = body.strength
    adjusted_speed = body.speed_modifier

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(f"{LEG_FORCE_LIMITS_URL}/evaluate", json={
                "intent": body.intent,
                "leg": body.target_leg,
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

    # Шаг 3: leg_movement_system
    movement_result = None
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(f"{LEG_MOVEMENT_URL}/execute", json={
                "leg": body.target_leg,
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
        logger.error(f"Leg movement unavailable: {e}")
        movement_result = {"error": str(e)}

    logger.info(
        f"Processed: leg={body.target_leg}, intent={body.intent}, "
        f"strength={adjusted_strength}"
    )

    return {
        "ok": True,
        "action": "executed",
        "verified_intent": body.intent,
        "verified_target": body.target_leg,
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