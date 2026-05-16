import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from kafka_bus import EventBus, TOPIC_EMERGENCY

HOST = "0.0.0.0"
PORT = 9105
MODULE_NAME = "leg_force_limits_system"
REQUEST_TIMEOUT = 5.0

CRITICAL_SENSORS_URL = os.getenv(
    "CRITICAL_SENSORS_LEGS_URL",
    "http://localhost:7102"
)

MAX_KNEE_DEG = float(os.getenv("MAX_KNEE_DEG", "170"))
MAX_HIP_FLEX_DEG = float(os.getenv("MAX_HIP_FLEX_DEG", "125"))
MAX_CONTACT_PRESSURE_N = float(os.getenv("MAX_LEG_CONTACT_PRESSURE_N", "220"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger(MODULE_NAME)

bus = EventBus(client_id=MODULE_NAME)


class EvaluateBody(BaseModel):
    intent: str = "idle"
    leg: str = "none"
    strength: float = 0.0
    speed_modifier: float = 0.0


app = FastAPI(title="Leg force & limits", version="1.0")


def _trigger_emergency(reason: str):
    bus.publish(TOPIC_EMERGENCY, {
        "source": MODULE_NAME,
        "reason": reason,
    })
    logger.error("Emergency published from leg force limits: %s", reason)


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.get("/status")
def status():
    return {
        "service": MODULE_NAME,
        "port": PORT,
        "limits": {
            "max_knee_deg": MAX_KNEE_DEG,
            "max_hip_flex_deg": MAX_HIP_FLEX_DEG,
            "max_contact_pressure_n": MAX_CONTACT_PRESSURE_N,
        },
        "critical_sensors_url": CRITICAL_SENSORS_URL,
    }


@app.post("/evaluate")
def evaluate(body: EvaluateBody):
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            snap = c.get(f"{CRITICAL_SENSORS_URL}/snapshot")
            snap.raise_for_status()
            snap = snap.json()
    except Exception as e:
        logger.error("Critical leg sensors unavailable: %s", e)
        return {
            "ok": False,
            "error": str(e),
            "stop_system": False
        }

    readings = snap.get("readings") or {}

    if not readings.get("trusted", True):
        _trigger_emergency("leg_sensors_untrusted")
        return {
            "ok": False,
            "stop_system": True,
            "reason": "sensors_untrusted"
        }

    knee = max(
        float(readings.get("knee_left_deg", 0)),
        float(readings.get("knee_right_deg", 0)),
    )
    hip = max(
        float(readings.get("hip_left_deg", 0)),
        float(readings.get("hip_right_deg", 0)),
    )
    pressure = max(
        float(readings.get("pressure_contact_left_n", 0)),
        float(readings.get("pressure_contact_right_n", 0)),
    )

    if knee > MAX_KNEE_DEG or hip > MAX_HIP_FLEX_DEG:
        _trigger_emergency("leg_angle_limit_exceeded")
        return {
            "ok": False,
            "stop_system": True,
            "reason": "leg_angle_limit",
            "readings": {
                "knee_max": knee,
                "hip_max": hip,
            },
        }

    clamped_strength = float(body.strength)
    speed = abs(float(body.speed_modifier))

    if pressure > MAX_CONTACT_PRESSURE_N:
        _trigger_emergency("leg_ground_pressure_exceeded")
        return {
            "ok": False,
            "stop_system": True,
            "reason": "ground_pressure_emergency"
        }

    if speed > 85 and clamped_strength > 70:
        clamped_strength *= 0.5
        logger.warning("Leg speed/strength clamp applied")

    return {
        "ok": True,
        "stop_system": False,
        "adjusted_command": {
            "leg": body.leg,
            "intent": body.intent,
            "strength": clamped_strength,
            "speed_modifier": body.speed_modifier,
        },
        "critical_readings": {
            "knee_max": knee,
            "hip_max": hip,
            "pressure_max": pressure,
        },
    }


if __name__ == "__main__":
    logger.info("Starting %s on %s:%s", MODULE_NAME, HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT)