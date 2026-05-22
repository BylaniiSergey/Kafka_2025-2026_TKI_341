import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
import logging
import threading
import time
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

from kafka_bus import EventBus, TOPIC_SENSORS_RAW
from logging_config import setup_logging

# ── Конфигурация ──────────────────────────────────────────────────────────────

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 6003))
MODULE_NAME = os.getenv('MODULE_NAME', 'sensors_module')

# Интервал публикации в Kafka — 20 секунд
PUBLISH_INTERVAL_S = float(os.getenv('PUBLISH_INTERVAL_S', '20.0'))

# ── Логирование ───────────────────────────────────────────────────────────────

setup_logging()
logger = logging.getLogger(MODULE_NAME)

# ── Kafka ─────────────────────────────────────────────────────────────────────

bus = EventBus(client_id=MODULE_NAME)

# ── База данных ───────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///sensors_module.db')
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

# ── Состояние симулятора ──────────────────────────────────────────────────────

_angle = 45.0
_velocity = 0.0
_max_torque = 50.0
_state_lock = threading.Lock()


def _simulate_sensors():
    """Симулирует физику датчиков на высокой частоте (20 Гц)."""
    global _angle, _velocity
    while True:
        time.sleep(0.05)
        with _state_lock:
            _velocity += (random.random() - 0.5) * 10
            _velocity = max(-100, min(100, _velocity))
            _angle += _velocity * 0.05
            _angle = max(0, min(150, _angle))


def _get_current_readings() -> dict:
    """Возвращает текущие показания всех датчиков."""
    with _state_lock:
        angle = _angle
        velocity = _velocity
    return {
        'joint_angle': round(angle, 2),
        'joint_angular_velocity': round(velocity, 2),
        'torque': round(20.0 + random.random() * 10, 2),
        'imu_roll': round(random.random() * 5 - 2.5, 3),
        'imu_pitch': round(random.random() * 10 - 5, 3),
        'imu_yaw': round(random.random() * 3 - 1.5, 3),
        'motor_temp': round(35.0 + random.random() * 10, 1),
    }


def _publish_readings_loop():
    """
    Публикует данные датчиков в Kafka каждые PUBLISH_INTERVAL_S секунд.
    Логирует каждую публикацию для контроля.
    """
    publish_count = 0
    logger.info(
        "Publish loop started, interval=%ds", PUBLISH_INTERVAL_S
    )

    while True:
        time.sleep(PUBLISH_INTERVAL_S)

        readings = _get_current_readings()
        success = bus.publish(TOPIC_SENSORS_RAW, readings)
        publish_count += 1

        logger.info(
            "Publish #%d %s | angle=%.1f vel=%.1f temp=%.1f",
            publish_count,
            "OK" if success else "FAIL",
            readings['joint_angle'],
            readings['joint_angular_velocity'],
            readings['motor_temp'],
        )


# ── Запуск фоновых потоков ────────────────────────────────────────────────────

threading.Thread(target=_simulate_sensors, daemon=True, name="sim").start()
threading.Thread(target=_publish_readings_loop, daemon=True, name="pub").start()


# ── Pydantic модели ───────────────────────────────────────────────────────────

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


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _save_reading(readings: dict):
    """Сохраняет показания в SQLite."""
    session = SessionLocal()
    try:
        db_fields = {
            k: v for k, v in readings.items()
            if k in (
                'joint_angle', 'joint_angular_velocity',
                'torque', 'imu_roll', 'imu_pitch', 'imu_yaw', 'motor_temp',
            )
        }
        session.add(SensorReadingDB(**db_fields))
        session.commit()
    except Exception as e:
        logger.error("DB save failed: %s", e)
        session.rollback()
    finally:
        session.close()


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Sensors Module", version="2.1")


@app.get('/health')
def health():
    return {
        'status': 'ok',
        'service': MODULE_NAME,
        'publish_interval_s': PUBLISH_INTERVAL_S,
    }


@app.get('/readings', response_model=SensorReadings)
def get_readings():
    readings = _get_current_readings()
    _save_reading(readings)
    return SensorReadings(
        **readings,
        timestamp=datetime.now().isoformat(),
    )


@app.post('/set_max_torque')
def set_max_torque(body: MaxTorqueRequest):
    global _max_torque
    _max_torque = body.max_torque
    logger.info("Max torque set to %.2f", _max_torque)
    return {'status': 'ok', 'max_torque': _max_torque}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        rows = (
            session.query(SensorReadingDB)
            .order_by(SensorReadingDB.created_at.desc())
            .limit(limit)
            .all()
        )
        return [{
            'id': r.id,
            'joint_angle': r.joint_angle,
            'joint_angular_velocity': r.joint_angular_velocity,
            'torque': r.torque,
            'imu_roll': r.imu_roll,
            'imu_pitch': r.imu_pitch,
            'imu_yaw': r.imu_yaw,
            'motor_temp': r.motor_temp,
            'created_at': r.created_at.strftime('%Y-%m-%d %H:%M:%S')
            if r.created_at else None,
        } for r in rows]
    finally:
        session.close()


# ── Запуск ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logger.info("Starting %s on %s:%d", MODULE_NAME, HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, access_log=False)