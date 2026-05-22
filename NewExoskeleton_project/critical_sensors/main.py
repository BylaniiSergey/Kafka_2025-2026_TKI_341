import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
import logging
import time
import threading
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float, Boolean, DateTime,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from logging_config import setup_logging

# ── Конфигурация ──────────────────────────────────────────────────────────────

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 4003))
MODULE_NAME = os.getenv('MODULE_NAME', 'critical_sensors')

# ── Логирование ───────────────────────────────────────────────────────────────

setup_logging()
logger = logging.getLogger(MODULE_NAME)

# ── База данных ───────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///critical_sensors.db')
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class CriticalReadingDB(Base):
    __tablename__ = 'critical_readings'
    id = Column(Integer, primary_key=True)
    joint_angle = Column(Float)
    joint_angular_velocity = Column(Float)
    imu_accel_x = Column(Float)
    imu_accel_y = Column(Float)
    balance_deviation = Column(Float)
    hardware_verified = Column(Boolean, default=True)
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


Base.metadata.create_all(engine)

# ── Состояние симулятора ──────────────────────────────────────────────────────

_state = {
    'joint_angle': 45.0,
    'joint_angular_velocity': 0.0,
    'imu_accel_x': 0.0,
    'imu_accel_y': 0.0,
    'balance_deviation': 0.0,
    'hardware_verified': True,
    'timestamp': datetime.now(timezone.utc).isoformat(),
}
_state_lock = threading.Lock()


def _simulate_critical_sensors():
    """
    Симулирует критические датчики.
    Имитирует те же физические величины что и sensors_module,
    но через независимый аппаратный канал.
    Добавляет небольшой гауссовский шум (как у реальных датчиков).
    """
    base_angle = 45.0
    base_velocity = 0.0

    while True:
        time.sleep(0.1)  # 10 Гц

        with _state_lock:
            base_velocity += (random.random() - 0.5) * 10
            base_velocity = max(-100, min(100, base_velocity))
            base_angle += base_velocity * 0.05
            base_angle = max(0, min(150, base_angle))

            # Гауссовский шум ±0.2° — в пределах допуска
            _state['joint_angle'] = round(
                base_angle + random.gauss(0, 0.2), 2
            )
            _state['joint_angular_velocity'] = round(
                base_velocity + random.gauss(0, 0.5), 2
            )
            _state['imu_accel_x'] = round(random.gauss(0, 0.1), 3)
            _state['imu_accel_y'] = round(random.gauss(0, 0.1), 3)
            _state['balance_deviation'] = round(random.gauss(0, 0.3), 3)
            _state['timestamp'] = datetime.now(timezone.utc).isoformat()


threading.Thread(
    target=_simulate_critical_sensors, daemon=True, name="crit-sim"
).start()


# ── Pydantic модели ───────────────────────────────────────────────────────────

class CriticalReadings(BaseModel):
    joint_angle: float
    joint_angular_velocity: float
    imu_accel_x: float
    imu_accel_y: float
    balance_deviation: float
    hardware_verified: bool = True
    timestamp: str


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _save_reading(data: dict):
    """Сохраняет показания в SQLite."""
    session = SessionLocal()
    try:
        session.add(CriticalReadingDB(
            joint_angle=data['joint_angle'],
            joint_angular_velocity=data['joint_angular_velocity'],
            imu_accel_x=data['imu_accel_x'],
            imu_accel_y=data['imu_accel_y'],
            balance_deviation=data['balance_deviation'],
            hardware_verified=data['hardware_verified'],
        ))
        session.commit()
    except Exception as e:
        logger.error("DB save failed: %s", e)
        session.rollback()
    finally:
        session.close()


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Critical Sensors", version="3.1")


@app.get('/health')
def health():
    return {'status': 'healthy', 'module': MODULE_NAME}


@app.get('/readings', response_model=CriticalReadings)
def get_readings():
    with _state_lock:
        data = dict(_state)
    _save_reading(data)
    return CriticalReadings(**data)


@app.get('/status')
def status():
    with _state_lock:
        data = dict(_state)
    return {
        'service': MODULE_NAME,
        'update_rate_hz': 10,
        'hardware_verified': data['hardware_verified'],
        'last_update': data['timestamp'],
        'readings': data,
    }


@app.post('/reset')
def reset():
    return {'ok': True}


# ── Запуск ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logger.info("Starting %s on %s:%d", MODULE_NAME, HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, access_log=False)