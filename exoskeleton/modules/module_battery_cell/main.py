from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import asyncio
from typing import Optional

app = FastAPI(title="Battery Cell Module (2.2.2)")

class BatteryState(BaseModel):
    soc: float  # State of Charge, %
    soh: float  # State of Health, %
    voltage: float  # V
    current: float  # A
    temperature: float  # °C

battery = {
    "soc": 85.0,
    "soh": 98.0,
    "voltage": 25.2,
    "current": 0.0,
    "temperature": 28.5
}

@app.get("/status", response_model=BatteryState)
async def get_status():
    """Запрос состояния батареи"""
    return battery

@app.post("/discharge")
async def discharge(current_ma: float, duration_ms: int):
    """Симуляция разряда"""
    global battery
    # Расчёт: delta_soc = (current_ma * duration_ms/3600) / capacity_mAh * 100
    delta_soc = (current_ma * (duration_ms / 1000 / 3600)) / 10000 * 100
    battery["soc"] = max(0, battery["soc"] - delta_soc)
    battery["current"] = current_ma / 1000  # mA -> A
    battery["temperature"] += delta_soc * 0.1
    return {"status": "discharging", "new_soc": battery["soc"]}

@app.post("/charge")
async def charge(current_ma: float, duration_ms: int):
    """Симуляция заряда"""
    global battery
    delta_soc = (current_ma * (duration_ms / 1000 / 3600)) / 10000 * 100
    battery["soc"] = min(100, battery["soc"] + delta_soc)
    battery["current"] = -current_ma / 1000
    return {"status": "charging", "new_soc": battery["soc"]}

