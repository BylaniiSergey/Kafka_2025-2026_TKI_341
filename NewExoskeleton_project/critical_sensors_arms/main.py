# critical_sensors_arms/main.py
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
PORT = int(os.getenv("PORT", "7101"))
MODULE_NAME = os.getenv("MODULE_NAME", "critical_sensors_arms")

UPPER_ARM_URL     = os.getenv("UPPER_ARM_URL",     "http://localhost:8003")
MIDDLE_ARM_URL    = os.getenv("MIDDLE_ARM_URL",    "http://localhost:8004")
FINGERS_URL       = os.getenv("FINGERS_URL",       "http://localhost:8005")
FORCE_CONTROL_URL = os.getenv("FORCE_CONTROL_URL", "http://localhost:8006")

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "0.5"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(MODULE_NAME)

_trusted = True


# ── Pydantic модели ───────────────────────────────────────────────────────────

class TrustedUpdate(BaseModel):
    trusted: Optional[bool] = None


# ── HTTP-клиент (патчится в тестах) ──────────────────────────────────────────

def get_client() -> httpx.Client:
    """
    Фабрика HTTP-клиента.
    Патчится в тестах через patch.object(mod, 'get_client', ...).
    Таймаут 0.5с — при недоступном приводе не зависаем надолго.
    """
    return httpx.Client(timeout=REQUEST_TIMEOUT)


# ── Опрос приводов ────────────────────────────────────────────────────────────

def _poll_drive_states() -> Dict[str, Any]:
    """
    Опрашивает реальное состояние приводов рук.
    Использует get_client() — патчится в тестах.
    Один клиент на все запросы.
    """
    state = {}

    with get_client() as c:

        # Верхний отдел (плечо)
        for arm in ["left", "right"]:
            try:
                resp = c.get(f"{UPPER_ARM_URL}/positions/{arm}")
                if resp.status_code == 200:
                    data = resp.json()
                    state[f"upper_{arm}"] = {
                        "positions": data.get("positions", {}),
                        "status":    data.get("status", "unknown"),
                    }
            except Exception as e:
                logger.debug(
                    f"Cannot poll upper_arm/{arm}: {e}"
                )
                state[f"upper_{arm}_error"] = str(e)

        # Средний отдел (локоть)
        for arm in ["left", "right"]:
            try:
                resp = c.get(f"{MIDDLE_ARM_URL}/positions/{arm}")
                if resp.status_code == 200:
                    data = resp.json()
                    state[f"middle_{arm}"] = {
                        "positions": data.get("positions", {}),
                        "status":    data.get("status", "unknown"),
                    }
            except Exception as e:
                logger.debug(
                    f"Cannot poll middle_arm/{arm}: {e}"
                )
                state[f"middle_{arm}_error"] = str(e)

        # Пальцы
        try:
            resp = c.get(f"{FINGERS_URL}/status")
            if resp.status_code == 200:
                data = resp.json()
                for arm in ["left", "right"]:
                    if arm in data:
                        state[f"fingers_{arm}"] = {
                            "grip_percentage": data[arm].get(
                                "grip_percentage", 0.0
                            ),
                            "grip_force": data[arm].get(
                                "grip_force", 0.0
                            ),
                            "status": data[arm].get(
                                "status", "unknown"
                            ),
                        }
        except Exception as e:
            logger.debug(f"Cannot poll fingers: {e}")
            state["fingers_error"] = str(e)

        # Контроль силы
        try:
            resp = c.get(f"{FORCE_CONTROL_URL}/status")
            if resp.status_code == 200:
                data = resp.json()
                for arm in ["left", "right"]:
                    if arm in data:
                        state[f"force_{arm}"] = {
                            "current_force": data[arm].get(
                                "current_force", 0.0
                            ),
                            "status": data[arm].get(
                                "status", "unknown"
                            ),
                        }
        except Exception as e:
            logger.debug(f"Cannot poll force_control: {e}")
            state["force_error"] = str(e)

    return state


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Critical sensors — arms", version="3.2")


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.get("/snapshot")
def snapshot():
    """
    Возвращает снимок состояния всех приводов рук.
    Используется arm_force_limits_system как канал А.
    """
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