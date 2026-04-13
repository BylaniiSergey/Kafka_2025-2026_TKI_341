# temperature_system/main.py
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Float, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 7003))
MODULE_NAME = os.getenv('MODULE_NAME', 'temperature_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///temperature_system.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class ClimateMode(str, Enum):
    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"


class TemperatureReadingDB(Base):
    __tablename__ = 'temperature_readings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    body_temp_c = Column(Float)
    air_temp_c = Column(Float)
    sensor_trusted = Column(Boolean)
    mode = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


@dataclass
class InternalTemperatureControl:
    body_temp_c: float = 36.6
    air_temp_c: float = 22.0
    body_min: float = 30.0
    body_max: float = 42.0
    air_min: float = 5.0
    air_max: float = 50.0
    target_body_low: float = 35.5
    target_body_high: float = 37.2
    target_air_high: float = 28.0
    target_air_low: float = 18.0
    sensor_trusted: bool = True
    mode: ClimateMode = ClimateMode.IDLE
    log: list = field(default_factory=list)

    def update_sensors(self, body_c: float, air_c: float) -> bool:
        ok = (
            self.body_min <= body_c <= self.body_max
            and self.air_min <= air_c <= self.air_max
        )
        self.sensor_trusted = ok
        if ok:
            self.body_temp_c = body_c
            self.air_temp_c = air_c
            self.log.append(
                f"Датчики ОК: тело={body_c:.1f}°C, воздух={air_c:.1f}°C"
            )
            logger.info(f"Sensors updated: body={body_c}, air={air_c}")
        else:
            self.log.append("Тревога: показания вне допустимого диапазона")
            logger.warning(
                f"Sensor out of range: body={body_c}, air={air_c}"
            )
        return ok

    def decide_mode(self) -> ClimateMode:
        if not self.sensor_trusted:
            self.mode = ClimateMode.IDLE
            return self.mode

        if (self.body_temp_c < self.target_body_low
                or self.air_temp_c < self.target_air_low):
            self.mode = ClimateMode.HEATING
        elif (self.body_temp_c > self.target_body_high
              or self.air_temp_c > self.target_air_high):
            self.mode = ClimateMode.COOLING
        else:
            self.mode = ClimateMode.IDLE

        self.log.append(f"Режим климата: {self.mode.value}")
        logger.info(f"Climate mode decided: {self.mode.value}")
        return self.mode

    def snapshot(self) -> dict:
        return {
            'service': 'temperature',
            'body_temp_c': self.body_temp_c,
            'air_temp_c': self.air_temp_c,
            'sensor_trusted': self.sensor_trusted,
            'mode': self.mode.value,
            'log_tail': self.log[-8:]
        }


_mod = InternalTemperatureControl()


def save_reading():
    session = SessionLocal()
    try:
        session.add(TemperatureReadingDB(
            body_temp_c=_mod.body_temp_c,
            air_temp_c=_mod.air_temp_c,
            sensor_trusted=_mod.sensor_trusted,
            mode=_mod.mode.value
        ))
        session.commit()
    finally:
        session.close()


class SensorsRequest(BaseModel):
    body_temp_c: float
    air_temp_c: float


app = FastAPI(title="Temperature System", version="2.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def status():
    return _mod.snapshot()


@app.post('/sensors')
def sensors(body: SensorsRequest):
    ok = _mod.update_sensors(body.body_temp_c, body.air_temp_c)
    save_reading()
    return {'ok': ok, 'state': _mod.snapshot()}


@app.post('/decide')
def decide():
    mode = _mod.decide_mode()
    save_reading()
    return {
        'ok': True,
        'climate_mode': mode.value,
        'state': _mod.snapshot()
    }


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        readings = (
            session.query(TemperatureReadingDB)
            .order_by(TemperatureReadingDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': r.id,
            'body_temp_c': r.body_temp_c,
            'air_temp_c': r.air_temp_c,
            'sensor_trusted': r.sensor_trusted,
            'mode': r.mode,
            'created_at': r.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if r.created_at else None
        } for r in readings]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)