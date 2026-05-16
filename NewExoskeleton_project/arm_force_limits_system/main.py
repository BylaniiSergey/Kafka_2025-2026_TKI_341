import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from kafka_bus import EventBus, TOPIC_EMERGENCY

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "7106"))
MODULE_NAME = os.getenv("MODULE_NAME", "arm_force_limits_system")

CRITICAL_SENSORS_URL = os.getenv("CRITICAL_SENSORS_ARMS_URL", "http://localhost:7101")
STOP_MODULE_URL = os.getenv("STOP_MODULE_URL", "http://localhost:7001")

bus = EventBus(client_id=MODULE_NAME)

MAX_SHOULDER_DEG = float(os.getenv("MAX_SHOULDER_DEG", "150"))
MAX_ELBOW_DEG = float(os.getenv("MAX_ELBOW_DEG", "150"))
MAX_PRESSURE_N = float(os.getenv("MAX_ARM_PRESSURE_N", "180"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(MODULE_NAME)


class EvaluateBody(BaseModel):
    intent: str = "idle"
    arm: str = "none"
    strength: float = 0.0
    speed_modifier: float = 0.0


app = FastAPI(title="Arm force & limits", version="1.0")


def _trigger_emergency(reason: str):
    bus.publish(TOPIC_EMERGENCY, {"source": MODULE_NAME, "reason": reason})
    logger.error("Emergency published from arm force limits: %s", reason)


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.post("/evaluate")
def evaluate(body: EvaluateBody):
    try:
        with httpx.Client(timeout=5.0) as c:
            snap = c.get(f"{CRITICAL_SENSORS_URL}/snapshot").json()
    except Exception as e:
        return {"ok": False, "error": str(e), "stop_system": False}
    readings = snap.get("readings") or {}
    if not readings.get("trusted", True):
        _trigger_emergency("critical_arm_sensors_untrusted")
        return {"ok": False, "stop_system": True, "reason": "sensors_untrusted"}
    el = max(float(readings.get("elbow_left_deg", 0)), float(readings.get("elbow_right_deg", 0)))
    sh = max(float(readings.get("shoulder_left_deg", 0)), float(readings.get("shoulder_right_deg", 0)))
    pr = max(float(readings.get("pressure_left_n", 0)), float(readings.get("pressure_right_n", 0)))
    if el > MAX_ELBOW_DEG or sh > MAX_SHOULDER_DEG:
        _trigger_emergency("joint_angle_exceeded")
        return {"ok": False, "stop_system": True, "reason": "angle_limit",
                "readings": {"elbow_max": el, "shoulder_max": sh}}
    clamped_strength = float(body.strength)
    if pr > MAX_PRESSURE_N:
        _trigger_emergency("pressure_exceeded_critical")
        return {"ok": False, "stop_system": True, "reason": "pressure_emergency"}
    if pr > MAX_PRESSURE_N * 0.65:
        clamped_strength *= 0.55
    return {"ok": True, "stop_system": False,
            "adjusted_command": {"arm": body.arm, "intent": body.intent,
                                  "strength": clamped_strength, "speed_modifier": body.speed_modifier},
            "critical_readings": {"elbow_max": el, "shoulder_max": sh, "pressure_max": pr}}


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)