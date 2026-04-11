from fastapi import FastAPI
from pydantic import BaseModel
import random
from datetime import datetime

app = FastAPI(title="Sensors Module (2.1)")

class SensorReadings(BaseModel):
    joint_angle: float
    joint_angular_velocity: float
    torque: float
    imu_roll: float
    imu_pitch: float
    imu_yaw: float
    motor_temp: float
    timestamp: str

angle = 45.0
velocity = 0.0

def simulate_sensors():
    global angle, velocity
    import time
    while True:
        time.sleep(0.05)
        velocity += (random.random() - 0.5) * 10
        velocity = max(-100, min(100, velocity))
        angle += velocity * 0.05
        angle = max(0, min(150, angle))

import threading
threading.Thread(target=simulate_sensors, daemon=True).start()

@app.get("/readings", response_model=SensorReadings)
async def get_readings():
    return SensorReadings(
        joint_angle=angle,
        joint_angular_velocity=velocity,
        torque=20.0 + random.random() * 10,
        imu_roll=random.random() * 5 - 2.5,
        imu_pitch=random.random() * 10 - 5,
        imu_yaw=random.random() * 3 - 1.5,
        motor_temp=35.0 + random.random() * 10,
        timestamp=datetime.now().isoformat()
    )

@app.post("/set_max_torque")
async def set_max_torque(max_torque: float):
    return {"status": "ok", "max_torque": max_torque}
