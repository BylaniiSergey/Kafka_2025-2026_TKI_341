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
PORT = int(os.getenv("PORT", "7102"))
MODULE_NAME = os.getenv("MODULE_NAME", "critical_sensors_legs")

KNEE_BELT_URL         = os.getenv("KNEE_BELT_URL",         "http://localhost:9003")
TRACK_SYSTEM_URL      = os.getenv("TRACK_SYSTEM_URL",      "http://localhost:9004")
LEG_FORCE_CONTROL_URL = os.getenv("LEG_FORCE_CONTROL_URL", "http://localhost:9006")

# Уменьшаем таймаут: 6 запросов × 0.5с = 3с вместо 18с
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "0.5"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(MODULE_NAME)

_trusted = True


class TrustedUpdate(BaseModel):
    trusted: Optional[bool] = None


def _poll_drive_states() -> Dict[str, Any]:
    """
    Опрашивает реальное состояние приводов ног.
    Один клиент на все запросы, таймаут 0.5с.
    """
    state = {}

    with httpx.Client(timeout=REQUEST_TIMEOUT) as c:

        # Коленный пояс
        for leg in ["left", "right"]:
            try:
                resp = c.get(f"{KNEE_BELT_URL}/positions/{leg}")
                if resp.status_code == 200:
                    data = resp.json()
                    state[f"knee_{leg}"] = {
                        "angle":     data.get("angle", 0.0),
                        "is_locked": data.get("is_locked", False),
                        "status":    data.get("status", "unknown"),
                    }
            except Exception as e:
                state[f"knee_{leg}_error"] = str(e)

        # Гусеницы
        try:
            resp = c.get(f"{TRACK_SYSTEM_URL}/status")
            if resp.status_code == 200:
                data = resp.json()
                state["track"] = {
                    "status":      data.get("status", "unknown"),
                    "left_speed":  data.get(
                        "left_track", {}
                    ).get("speed", 0.0),
                    "right_speed": data.get(
                        "right_track", {}
                    ).get("speed", 0.0),
                }
        except Exception as e:
            state["track_error"] = str(e)

        # Контроль силы ног
        try:
            resp = c.get(f"{LEG_FORCE_CONTROL_URL}/status")
            if resp.status_code == 200:
                data = resp.json()
                for loc in [
                    "left_knee", "right_knee",
                    "left_track", "right_track",
                ]:
                    if loc in data:
                        state[f"force_{loc}"] = {
                            "current_torque": data[loc].get(
                                "current_torque", 0.0
                            ),
                            "current_force": data[loc].get(
                                "current_force", 0.0
                            ),
                            "status": data[loc].get("status", "unknown"),
                        }
        except Exception as e:
            state["force_error"] = str(e)

    return state


app = FastAPI(title="Critical sensors — legs", version="3.1")


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.get("/snapshot")
def snapshot():
    drives = _poll_drive_states()
    return {
        "service":      MODULE_NAME,
        "trusted":      _trusted,
        "drive_states": drives,
    }


@app.get("/drive_snapshot")
def drive_snapshot():
    """Алиас для обратной совместимости."""
    return snapshot()


@app.post("/set_trusted")
def set_trusted(body: TrustedUpdate):
    global _trusted
    if body.trusted is not None:
        _trusted = body.trusted
    return {"ok": True, "trusted": _trusted}


@app.post("/reset")
def reset():
    global _trusted
    _trusted = True
    return {"ok": True}


if __name__ == "__main__":
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)