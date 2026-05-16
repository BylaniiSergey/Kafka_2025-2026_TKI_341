import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
import logging
import threading
import time
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

from kafka_bus import EventBus, TOPIC_SENSORS_RAW

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 6003))
MODULE_NAME = os.getenv('MODULE_NAME', 'sensors_module')
PUBLISH_INTERVAL_S = float(os.getenv('PUBLISH_INTERVAL_S', '1.0'))

bus = EventBus(client_id=MODULE_NAME)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///sensors_module.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class SensorReadingDB(Base):
    __tablename__ = 'sensor_readings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    joint_angle = Column(Float)
    joint_angular_velocity = Column(Float)
    torque = Column(Float)
    imu_roll = Column(Float)
    imu_pitch = Column(Float)
    imu_yaw = Column(Float)
    motor_temp = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

_angle = 45.0
_velocity = 0.0
_max_torque = 50.0


def simulate_sensors():
    global _angle, _velocity
    while True:
        time.sleep(0.05)
        _velocity += (random.random() - 0.5) * 10
        _velocity = max(-100, min(100, _velocity))
        _angle += _velocity * 0.05
        _angle = max(0, min(150, _angle))


def publish_readings_loop():
    while True:
        time.sleep(PUBLISH_INTERVAL_S)
        bus.publish(TOPIC_SENSORS_RAW, {
            'joint_angle': round(_angle, 2),
            'joint_angular_velocity': round(_velocity, 2),
            'torque': round(20.0 + random.random() * 10, 2),
            'imu_roll': round(random.random() * 5 - 2.5, 3),
            'imu_pitch': round(random.random() * 10 - 5, 3),
            'imu_yaw': round(random.random() * 3 - 1.5, 3),
            'motor_temp': round(35.0 + random.random() * 10, 1),
        })


threading.Thread(target=simulate_sensors, daemon=True).start()
threading.Thread(target=publish_readings_loop, daemon=True).start()


class SensorReadings(BaseModel):
    joint_angle: float
    joint_angular_velocity: float
    torque: float
    imu_roll: float
    imu_pitch: float
    imu_yaw: float
    motor_temp: float
    timestamp: str


class MaxTorqueRequest(BaseModel):
    max_torque: float


def save_reading():
    session = SessionLocal()
    try:
        session.add(SensorReadingDB(
            joint_angle=_angle, joint_angular_velocity=_velocity,
            torque=20.0 + random.random() * 10,
            imu_roll=random.random() * 5 - 2.5,
            imu_pitch=random.random() * 10 - 5,
            imu_yaw=random.random() * 3 - 1.5,
            motor_temp=35.0 + random.random() * 10
        ))
        session.commit()
    finally:
        session.close()


app = FastAPI(title="Sensors Module", version="2.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/readings', response_model=SensorReadings)
def get_readings():
    readings = SensorReadings(
        joint_angle=round(_angle, 2),
        joint_angular_velocity=round(_velocity, 2),
        torque=round(20.0 + random.random() * 10, 2),
        imu_roll=round(random.random() * 5 - 2.5, 3),
        imu_pitch=round(random.random() * 10 - 5, 3),
        imu_yaw=round(random.random() * 3 - 1.5, 3),
        motor_temp=round(35.0 + random.random() * 10, 1),
        timestamp=datetime.now().isoformat()
    )
    save_reading()
    return readings


@app.post('/set_max_torque')
def set_max_torque(body: MaxTorqueRequest):
    global _max_torque
    _max_torque = body.max_torque
    return {'status': 'ok', 'max_torque': _max_torque}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        readings = session.query(SensorReadingDB).order_by(SensorReadingDB.created_at.desc()).limit(limit).all()
        return [{'id': r.id, 'joint_angle': r.joint_angle, 'joint_angular_velocity': r.joint_angular_velocity,
                 'torque': r.torque, 'imu_roll': r.imu_roll, 'imu_pitch': r.imu_pitch,
                 'imu_yaw': r.imu_yaw, 'motor_temp': r.motor_temp,
                 'created_at': r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else None} for r in readings]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)