from fastapi import FastAPI
from pydantic import BaseModel
from datetime import datetime

app = FastAPI(title="Charger Module (2.2.1)")

class ChargerStatus(BaseModel):
    plugged: bool
    enabled: bool
    voltage: float
    current_ma: float
    timestamp: str

charger = {
    "plugged": True,
    "enabled": False,
    "voltage": 29.4,
    "current_ma": 2000
}

@app.get("/status", response_model=ChargerStatus)
async def get_status():
    return ChargerStatus(
        plugged=charger["plugged"],
        enabled=charger["enabled"],
        voltage=charger["voltage"],
        current_ma=charger["current_ma"],
        timestamp=datetime.now().isoformat()
    )

@app.post("/control")
async def control_charger(enabled: bool):
    charger["enabled"] = enabled
    return {"status": "ok", "enabled": enabled}

@app.post("/plug")
async def plug_charger(plugged: bool):
    charger["plugged"] = plugged
    if not plugged:
        charger["enabled"] = False
    return {"status": "ok", "plugged": plugged}
