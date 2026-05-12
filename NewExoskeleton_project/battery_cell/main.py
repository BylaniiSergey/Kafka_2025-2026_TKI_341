# battery_cell/main.py
import os
import logging
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 6006))
MODULE_NAME = os.getenv('MODULE_NAME', 'battery_cell')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///battery_cell.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class BatteryLogDB(Base):
    __tablename__ = 'battery_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event = Column(String(20))
    soc = Column(Float)
    soh = Column(Float)
    voltage = Column(Float)
    current = Column(Float)
    temperature = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

battery = {
    "soc": 85.0,
    "soh": 98.0,
    "voltage": 25.2,
    "current": 0.0,
    "temperature": 28.5
}


class BatteryState(BaseModel):
    soc: float
    soh: float
    voltage: float
    current: float
    temperature: float


class DischargeRequest(BaseModel):
    current_ma: float
    duration_ms: int


class ChargeRequest(BaseModel):
    current_ma: float
    duration_ms: int


def save_log(event: str):
    session = SessionLocal()
    try:
        session.add(BatteryLogDB(
            event=event,
            soc=battery['soc'],
            soh=battery['soh'],
            voltage=battery['voltage'],
            current=battery['current'],
            temperature=battery['temperature']
        ))
        session.commit()
    finally:
        session.close()


app = FastAPI(title="Battery Cell", version="2.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status', response_model=BatteryState)
def get_status():
    return battery


@app.post('/discharge')
def discharge(body: DischargeRequest):
    delta_soc = (
        body.current_ma * (body.duration_ms / 1000 / 3600)
    ) / 10000 * 100
    battery['soc'] = max(0, battery['soc'] - delta_soc)
    battery['current'] = body.current_ma / 1000
    battery['temperature'] += delta_soc * 0.1

    logger.info(
        f"Discharge: soc={battery['soc']:.1f}%, "
        f"temp={battery['temperature']:.1f}°C"
    )
    save_log('discharge')
    return {'status': 'discharging', 'new_soc': battery['soc']}


@app.post('/charge')
def charge(body: ChargeRequest):
    delta_soc = (
        body.current_ma * (body.duration_ms / 1000 / 3600)
    ) / 10000 * 100
    battery['soc'] = min(100, battery['soc'] + delta_soc)
    battery['current'] = -body.current_ma / 1000

    logger.info(f"Charge: soc={battery['soc']:.1f}%")
    save_log('charge')
    return {'status': 'charging', 'new_soc': battery['soc']}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(BatteryLogDB)
            .order_by(BatteryLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id, 'event': l.event,
            'soc': l.soc, 'soh': l.soh,
            'voltage': l.voltage, 'current': l.current,
            'temperature': l.temperature,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)