# leg_force_limits_system/main.py
import os
import logging

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "5309"))
MODULE_NAME = os.getenv(
    "MODULE_NAME", "leg_force_limits_system"
)

CRITICAL_SENSORS_URL = os.getenv(
    "CRITICAL_SENSORS_LEGS_URL", "http://localhost:5307"
)
EMERGENCY_CONTROL_URL = os.getenv(
    "EMERGENCY_CONTROL_URL", "http://localhost:5201"
)

MAX_KNEE_DEG = float(os.getenv("MAX_KNEE_DEG", "170"))
MAX_HIP_FLEX_DEG = float(
    os.getenv("MAX_HIP_FLEX_DEG", "125")
)
MAX_CONTACT_PRESSURE_N = float(
    os.getenv("MAX_LEG_CONTACT_PRESSURE_N", "220")
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger(MODULE_NAME)


class EvaluateBody(BaseModel):
    intent: str = "idle"
    leg: str = "none"
    strength: float = 0.0
    speed_modifier: float = 0.0


app = FastAPI(title="Leg force & limits", version="1.1")


def _trigger_emergency(reason: str):
    try:
        with httpx.Client(timeout=5.0) as c:
            c.post(
                f"{EMERGENCY_CONTROL_URL}/emergency",
                json={
                    "source": MODULE_NAME,
                    "reason": reason
                }
            )
        logger.error(f"Emergency triggered: {reason}")
    except Exception as e:
        logger.exception(
            f"Failed to reach emergency control: {e}"
        )


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.post("/evaluate")
def evaluate(body: EvaluateBody):
    try:
        with httpx.Client(timeout=5.0) as c:
            snap = c.get(
                f"{CRITICAL_SENSORS_URL}/snapshot"
            ).json()
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "stop_system": False
        }

    readings = snap.get("readings") or {}
    if not readings.get("trusted", True):
        _trigger_emergency("critical_leg_sensors_untrusted")
        return {
            "ok": False,
            "stop_system": True,
            "reason": "sensors_untrusted"
        }

    knee = max(
        float(readings.get("knee_left_deg", 0)),
        float(readings.get("knee_right_deg", 0))
    )
    hip = max(
        float(readings.get("hip_left_deg", 0)),
        float(readings.get("hip_right_deg", 0))
    )
    pr = max(
        float(readings.get("pressure_contact_left_n", 0)),
        float(readings.get("pressure_contact_right_n", 0))
    )

    if knee > MAX_KNEE_DEG or hip > MAX_HIP_FLEX_DEG:
        _trigger_emergency("leg_angle_limit_exceeded")
        return {
            "ok": False,
            "stop_system": True,
            "reason": "leg_angle_limit",
            "readings": {
                "knee_max": knee,
                "hip_max": hip
            }
        }

    if pr > MAX_CONTACT_PRESSURE_N:
        _trigger_emergency("ground_pressure_emergency")
        return {
            "ok": False,
            "stop_system": True,
            "reason": "ground_pressure_emergency"
        }

    clamped_strength = float(body.strength)
    spd = abs(float(body.speed_modifier))

    if spd > 85 and clamped_strength > 70:
        clamped_strength *= 0.5
        logger.warning(
            "Leg speed/strength clamp (risk of hazardous motion)"
        )

    return {
        "ok": True,
        "stop_system": False,
        "adjusted_command": {
            "leg": body.leg,
            "intent": body.intent,
            "strength": clamped_strength,
            "speed_modifier": body.speed_modifier
        },
        "critical_readings": {
            "knee_max": knee,
            "hip_max": hip,
            "pressure_max": pr
        }
    }


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)