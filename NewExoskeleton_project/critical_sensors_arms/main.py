# Критичные датчики в руках — резервный канал давления/углов для контроля силы рук (MM).
import os
import logging
from typing import Any, Dict

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "7101"))
MODULE_NAME = os.getenv("MODULE_NAME", "critical_sensors_arms")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(MODULE_NAME)

_state: Dict[str, Any] = {
    "trusted": True,
    "pressure_left_n": 0.0,
    "pressure_right_n": 0.0,
    "elbow_left_deg": 90.0,
    "elbow_right_deg": 90.0,
    "shoulder_left_deg": 45.0,
    "shoulder_right_deg": 45.0,
}


class SensorsUpdate(BaseModel):
    pressure_left_n: float | None = None
    pressure_right_n: float | None = None
    elbow_left_deg: float | None = None
    elbow_right_deg: float | None = None
    shoulder_left_deg: float | None = None
    shoulder_right_deg: float | None = None


app = FastAPI(title="Critical sensors — arms", version="1.0")


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
    logger.info("Critical arm sensors updated")
    return snapshot()


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)

