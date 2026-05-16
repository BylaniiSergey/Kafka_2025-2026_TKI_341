# Модуль верификации нейронных сигналов верхних конечностей (SS).
# Сравнивает текущую команду с предыдущей: без резких скачков, контекст (поза/режим).
import os
import logging
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "7103"))
MODULE_NAME = os.getenv("MODULE_NAME", "neural_verify_upper")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
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
    posture: str = "standing"


app = FastAPI(title="Neural verify — upper limbs", version="1.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.post("/session/init")
def session_init(body: SessionInit):
    global _registered_patient_id, _last
    _registered_patient_id = body.patient_id
    _last = None
    logger.info("Session patient registered for upper neural verify")
    return {"ok": True, "patient_id": _registered_patient_id}


@app.post("/verify")
def verify(body: VerifyRequest):
    global _last
    if not _registered_patient_id:
        return {"allowed": False, "reason": "session_not_initialized"}
    if body.patient_id != _registered_patient_id:
        logger.warning("Patient ID mismatch upper neural")
        return {"allowed": False, "reason": "patient_id_mismatch"}

    intent = body.intent.lower()
    posture = body.posture.lower()

    if (posture, intent) in POSTURE_BLOCKED:
        return {"allowed": False, "reason": "intent_inconsistent_with_posture"}

    if _last is not None:
        ds = abs(float(body.strength) - float(_last.get("strength", 0)))
        if ds > MAX_STRENGTH_JUMP:
            return {"allowed": False, "reason": "strength_jump_too_large"}

        prev_idle = _last.get("intent") == "idle"
        curr_active = intent not in ("idle", "release")
        if prev_idle and curr_active:
            if float(body.strength) > 70:
                return {"allowed": False, "reason": "idle_to_high_power_transition"}

    _last = {"intent": intent, "target": body.target, "strength": body.strength}
    return {"allowed": True, "reason": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)

