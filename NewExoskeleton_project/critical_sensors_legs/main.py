# Критичные датчики в ногах — резервный канал для контроля силы и ограничений ног (MM).
import os
import logging
from typing import Any, Dict

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "7102"))
MODULE_NAME = os.getenv("MODULE_NAME", "critical_sensors_legs")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(MODULE_NAME)

_state: Dict[str, Any] = {
    "trusted": True,
    "hip_left_deg": 10.0,
    "hip_right_deg": 10.0,
    "knee_left_deg": 90.0,
    "knee_right_deg": 90.0,
    "pressure_contact_left_n": 0.0,
    "pressure_contact_right_n": 0.0,
}


class SensorsUpdate(BaseModel):
    hip_left_deg: float | None = None
    hip_right_deg: float | None = None
    knee_left_deg: float | None = None
    knee_right_deg: float | None = None
    pressure_contact_left_n: float | None = None
    pressure_contact_right_n: float | None = None


app = FastAPI(title="Critical sensors — legs", version="1.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": MODULE_NAME}


@app.get("/snapshot")
def snapshot():
    return {"service": MODULE_NAME, "trusted": _state["trusted"], "readings": dict(_state)}


@app.post("/update")
def update(body: SensorsUpdate):
    for k, v in body.model_dump(exclude_none=True).items():
        _state[k] = v
    logger.info("Critical leg sensors updated")
    return snapshot()


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)

