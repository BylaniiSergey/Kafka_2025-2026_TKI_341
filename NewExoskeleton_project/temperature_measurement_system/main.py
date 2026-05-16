import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import logging
import threading

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from kafka_bus import EventBus, TOPIC_EMERGENCY

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "7105"))
MODULE_NAME = os.getenv("MODULE_NAME", "temperature_measurement_system")

BODY_CRITICAL_HIGH = float(os.getenv("BODY_CRITICAL_HIGH_C", "40.0"))
BODY_CRITICAL_LOW = float(os.getenv("BODY_CRITICAL_LOW_C", "35.0"))
AIR_CRITICAL_HIGH = float(os.getenv("AIR_CRITICAL_HIGH_C", "42.0"))
CHECK_INTERVAL_S = float(os.getenv("CHECK_INTERVAL_S", "3.0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(MODULE_NAME)

bus = EventBus(client_id=MODULE_NAME)

_body_c = 36.6
_air_c = 22.0
_trusted = True
_last_emergency_reason = None


class MeasureBody(BaseModel):
    body_temp_c: float
    air_temp_c: float


app = FastAPI(title="Temperature measurement", version="1.1")


def _evaluate():
    if not _trusted:
        return True, "sensor_untrusted"
    if _body_c >= BODY_CRITICAL_HIGH or _air_c >= AIR_CRITICAL_HIGH:
        return True, "thermal_overheat"
    if _body_c <= BODY_CRITICAL_LOW:
        return True, "hypothermia_risk"
    return False, None


def _watch_loop():
    global _last_emergency_reason
    while True:
        time.sleep(CHECK_INTERVAL_S)
        emergency, reason = _evaluate()
        if emergency and reason != _last_emergency_reason:
            _last_emergency_reason = reason
            logger.critical(f"Temperature emergency: {reason} body={_body_c}C air={_air_c}C")
            bus.publish(TOPIC_EMERGENCY, {'source': MODULE_NAME, 'reason': reason,
                                          'body_temp_c': _body_c, 'air_temp_c': _air_c})
        elif not emergency:
            _last_emergency_reason = None


threading.Thread(target=_watch_loop, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.post("/measure")
def measure(body: MeasureBody):
    global _body_c, _air_c, _trusted
    _body_c = body.body_temp_c
    _air_c = body.air_temp_c
    _trusted = 25.0 <= _body_c <= 45.0 and 0.0 <= _air_c <= 55.0
    return status()


@app.get("/status")
def status():
    emergency, reason = _evaluate()
    return {"service": MODULE_NAME, "body_temp_c": _body_c, "air_temp_c": _air_c,
            "trusted": _trusted, "emergency_recommended": emergency, "emergency_reason": reason}


if __name__ == "__main__":
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)