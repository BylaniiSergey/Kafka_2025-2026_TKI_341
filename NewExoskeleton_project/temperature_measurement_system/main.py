# temperature_measurement_system/main.py
import os
import logging

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "5304"))
MODULE_NAME = os.getenv(
    "MODULE_NAME", "temperature_measurement_system"
)

EMERGENCY_CONTROL_URL = os.getenv(
    "EMERGENCY_CONTROL_URL", "http://localhost:5201"
)
TEMPERATURE_SYSTEM_URL = os.getenv(
    "TEMPERATURE_SYSTEM_URL", "http://localhost:7003"
)

BODY_CRITICAL_HIGH = float(
    os.getenv("BODY_CRITICAL_HIGH_C", "40.0")
)
BODY_CRITICAL_LOW = float(
    os.getenv("BODY_CRITICAL_LOW_C", "35.0")
)
AIR_CRITICAL_HIGH = float(
    os.getenv("AIR_CRITICAL_HIGH_C", "42.0")
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger(MODULE_NAME)

_body_c = 36.6
_air_c = 22.0
_trusted = True


class MeasureBody(BaseModel):
    body_temp_c: float
    air_temp_c: float


def _signal_emergency(reason: str):
    try:
        with httpx.Client(timeout=3.0) as c:
            c.post(
                f"{EMERGENCY_CONTROL_URL}/emergency",
                json={
                    "source": MODULE_NAME,
                    "reason": reason
                }
            )
        logger.critical(f"Emergency signal sent: {reason}")
    except Exception as e:
        logger.error(f"Emergency signal failed: {e}")


def _forward_to_temperature_system(body_c: float, air_c: float):
    """
    Передаёт измеренные данные в систему терморегуляции.
    """
    try:
        with httpx.Client(timeout=3.0) as c:
            c.post(
                f"{TEMPERATURE_SYSTEM_URL}/sensors",
                json={
                    "body_temp_c": body_c,
                    "air_temp_c": air_c
                }
            )
            c.post(f"{TEMPERATURE_SYSTEM_URL}/decide")
        logger.info("Temperature data forwarded to temperature_system")
    except Exception as e:
        logger.error(f"Forward to temperature_system failed: {e}")


app = FastAPI(
    title="Temperature Measurement System", version="1.2"
)


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.get("/status")
def get_status():
    emergency = False
    reason = None

    if _trusted:
        if _body_c >= BODY_CRITICAL_HIGH or _air_c >= AIR_CRITICAL_HIGH:
            emergency = True
            reason = "thermal_overheat"
        elif _body_c <= BODY_CRITICAL_LOW:
            emergency = True
            reason = "hypothermia_risk"
    else:
        emergency = True
        reason = "sensor_untrusted"

    return {
        "service": MODULE_NAME,
        "body_temp_c": _body_c,
        "air_temp_c": _air_c,
        "trusted": _trusted,
        "emergency_recommended": emergency,
        "emergency_reason": reason
    }


@app.post("/measure")
def measure(body: MeasureBody):
    global _body_c, _air_c, _trusted

    _body_c = body.body_temp_c
    _air_c = body.air_temp_c
    _trusted = (
        25.0 <= _body_c <= 45.0
        and 0.0 <= _air_c <= 55.0
    )

    if not _trusted:
        logger.warning(
            "Temperature readings rejected as implausible"
        )

    # Передаём данные в систему терморегуляции
    _forward_to_temperature_system(_body_c, _air_c)

    result = get_status()

    # При опасности — напрямую в аварийный модуль
    if result.get("emergency_recommended"):
        _signal_emergency(
            result.get(
                "emergency_reason",
                "thermal_emergency"
            )
        )

    return result


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)