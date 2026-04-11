from fastapi import FastAPI, WebSocket
from typing import Dict
import httpx
import os

app = FastAPI(title="Comms Module (1)")

MONITORING_URL = os.getenv("MONITORING_URL", "http://monitoring:8000")

active_connections: Dict[str, WebSocket] = {}

@app.websocket("/doctor/{doctor_id}")
async def doctor_endpoint(websocket: WebSocket, doctor_id: str):
    """Врач подключается для получения телеметрии"""
    await websocket.accept()
    active_connections[f"doctor_{doctor_id}"] = websocket
    try:
        while True:
            async with httpx.AsyncClient() as client:
                telemetry = await client.get(f"{MONITORING_URL}/telemetry")
                await websocket.send_json(telemetry.json())
            await asyncio.sleep(1)
    except:
        del active_connections[f"doctor_{doctor_id}"]

@app.post("/alarm")
async def receive_alarm(alarm: dict):
    """Получить аларм от системы мониторинга и разослать"""
    for conn in active_connections.values():
        await conn.send_json({"type": "alarm", "data": alarm})
    return {"status": "alarm_sent"}

@app.post("/command")
async def command_from_doctor(command: dict):
    """Принять команду от врача и отправить в систему"""
    async with httpx.AsyncClient() as client:
        if command["type"] == "emergency_stop":
            await client.post(f"{MONITORING_URL}/emergency_stop")
        elif command["type"] == "set_max_torque":
            await client.post(f"{SENSORS_URL}/set_max_torque", json=command)
    return {"status": "command_processed"}
