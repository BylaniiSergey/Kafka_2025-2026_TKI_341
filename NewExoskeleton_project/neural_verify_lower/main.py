# Модуль верификации нейронных сигналов нижних конечностей (SS).
import os
import logging
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "7104"))
MODULE_NAME = os.getenv("MODULE_NAME", "neural_verify_lower")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(MODULE_NAME)

_registered_patient_id: Optional[str] = None
_last: Optional[Dict[str, Any]] = None

MAX_STRENGTH_JUMP = 60.0


class SessionInit(BaseModel):
    patient_id: str


class VerifyRequest(BaseModel):
    patient_id: str
    intent: str = "idle"
    target: str = "none"
    strength: float = 0.0
    posture: str = "standing"


app = FastAPI(title="Neural verify — lower limbs", version="1.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.post("/session/init")
def session_init(body: SessionInit):
    global _registered_patient_id, _last
    _registered_patient_id = body.patient_id
    _last = None
    logger.info("Session patient registered for lower neural verify")
    return {"ok": True, "patient_id": _registered_patient_id}


@app.post("/verify")
def verify(body: VerifyRequest):
    global _last
    if not _registered_patient_id:
        return {"allowed": False, "reason": "session_not_initialized"}
    if body.patient_id != _registered_patient_id:
        logger.warning("Patient ID mismatch lower neural")
        return {"allowed": False, "reason": "patient_id_mismatch"}

    intent = body.intent.lower()
    posture = body.posture.lower()

    if posture == "sitting" and intent in ("step_forward", "step_back", "walk"):
        return {"allowed": False, "reason": "locomotion_while_seated"}

    if _last is not None:
        ds = abs(float(body.strength) - float(_last.get("strength", 0)))
        if ds > MAX_STRENGTH_JUMP:
            return {"allowed": False, "reason": "strength_jump_too_large"}

    _last = {"intent": intent, "target": body.target, "strength": body.strength}
    return {"allowed": True, "reason": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)

