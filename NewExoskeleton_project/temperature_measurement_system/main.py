# Система измерения температуры внутренней части (SS): только измерение и признак аварии для аварийного модуля.
import os
import logging

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "7105"))
MODULE_NAME = os.getenv("MODULE_NAME", "temperature_measurement_system")

BODY_CRITICAL_HIGH = float(os.getenv("BODY_CRITICAL_HIGH_C", "40.0"))
BODY_CRITICAL_LOW = float(os.getenv("BODY_CRITICAL_LOW_C", "35.0"))
AIR_CRITICAL_HIGH = float(os.getenv("AIR_CRITICAL_HIGH_C", "42.0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(MODULE_NAME)

_body_c = 36.6
_air_c = 22.0
_trusted = True


class MeasureBody(BaseModel):
    body_temp_c: float
    air_temp_c: float


app = FastAPI(title="Temperature measurement", version="1.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.post("/measure")
def measure(body: MeasureBody):
    global _body_c, _air_c, _trusted
    _body_c = body.body_temp_c
    _air_c = body.air_temp_c
    _trusted = 25.0 <= _body_c <= 45.0 and 0.0 <= _air_c <= 55.0
    if not _trusted:
        logger.warning("Temperature readings rejected as implausible")
    return status()


@app.get("/status")
def status():
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
        "emergency_reason": reason,
    }


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
