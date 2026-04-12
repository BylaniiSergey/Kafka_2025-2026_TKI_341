from fastapi import FastAPI, BackgroundTasks
import httpx
import os

app = FastAPI(title="Monitoring System (2)")

SENSORS_URL = os.getenv("SENSORS_URL", "http://sensors:8000")
BATTERY_CTRL_URL = os.getenv("BATTERY_CTRL_URL", "http://battery_controller:8000")
COMMS_URL = os.getenv("COMMS_URL", "http://comms:8000")

last_readings = {}

@app.get("/health")
async def health():
    """Состояние системы мониторинга"""
    return {"status": "ok"}

@app.get("/telemetry")
async def get_telemetry():
    """Собрать свежие данные со всех датчиков"""
    async with httpx.AsyncClient() as client:
        sensors = await client.get(f"{SENSORS_URL}/readings")
        battery = await client.get(f"{BATTERY_CTRL_URL}/status")
        
        telemetry = {
            **sensors.json(),
            "battery": battery.json()
        }
        
        alarms = []
        if telemetry["joint_angle"] > 120:
            alarms.append("HYPEREXTENSION")
        if telemetry["battery"]["soc"] < 10:
            alarms.append("BATTERY_LOW")
        
        if alarms:
            await client.post(f"{COMMS_URL}/alarm", json={"alarms": alarms})
        
        return telemetry
