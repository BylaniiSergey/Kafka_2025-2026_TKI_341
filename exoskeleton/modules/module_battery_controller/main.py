from fastapi import FastAPI, HTTPException
import httpx
import os

app = FastAPI(title="Battery Controller (2.2)")

CHARGER_URL = os.getenv("CHARGER_URL", "http://charger:8000")
BATTERY_CELL_URL = os.getenv("BATTERY_CELL_URL", "http://battery_cell:8000")

@app.get("/status")
async def get_battery_status():
    """Агрегированный статус батареи"""
    async with httpx.AsyncClient() as client:
        charger_resp = await client.get(f"{CHARGER_URL}/status")
        cell_resp = await client.get(f"{BATTERY_CELL_URL}/status")
        
        charger_data = charger_resp.json()
        cell_data = cell_resp.json()
        
        return {
            "soc": cell_data["soc"],
            "soh": cell_data["soh"],
            "temperature": cell_data["temperature"],
            "charger_plugged": charger_data["plugged"],
            "charging_enabled": charger_data["enabled"]
        }

@app.post("/control/charge")
async def enable_charging(enable: bool):
    """Включить/выключить зарядку"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{CHARGER_URL}/control", json={"enabled": enable})
        return resp.json()
