# charger_module/main.py
import os
import logging
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float, Boolean, String, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 6005))
MODULE_NAME = os.getenv('MODULE_NAME', 'charger_module')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///charger_module.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class ChargerLogDB(Base):
    __tablename__ = 'charger_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event = Column(String(20))
    plugged = Column(Boolean)
    enabled = Column(Boolean)
    voltage = Column(Float)
    current_ma = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

charger = {
    "plugged": True,
    "enabled": False,
    "voltage": 29.4,
    "current_ma": 2000.0
}


class ChargerStatus(BaseModel):
    plugged: bool
    enabled: bool
    voltage: float
    current_ma: float
    timestamp: str


class ControlRequest(BaseModel):
    enabled: bool


class PlugRequest(BaseModel):
    plugged: bool


def save_log(event: str):
    session = SessionLocal()
    try:
        session.add(ChargerLogDB(
            event=event,
            plugged=charger['plugged'],
            enabled=charger['enabled'],
            voltage=charger['voltage'],
            current_ma=charger['current_ma']
        ))
        session.commit()
    finally:
        session.close()


app = FastAPI(title="Charger Module", version="2.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status', response_model=ChargerStatus)
def get_status():
    return ChargerStatus(
        plugged=charger['plugged'],
        enabled=charger['enabled'],
        voltage=charger['voltage'],
        current_ma=charger['current_ma'],
        timestamp=datetime.now().isoformat()
    )


@app.post('/control')
def control_charger(body: ControlRequest):
    charger['enabled'] = body.enabled
    logger.info(
        f"Charger {'enabled' if body.enabled else 'disabled'}"
    )
    save_log('control')
    return {'status': 'ok', 'enabled': body.enabled}


@app.post('/plug')
def plug_charger(body: PlugRequest):
    charger['plugged'] = body.plugged
    if not body.plugged:
        charger['enabled'] = False
    logger.info(
        f"Charger {'plugged' if body.plugged else 'unplugged'}"
    )
    save_log('plug')
    return {'status': 'ok', 'plugged': body.plugged}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(ChargerLogDB)
            .order_by(ChargerLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id, 'event': l.event,
            'plugged': l.plugged, 'enabled': l.enabled,
            'voltage': l.voltage, 'current_ma': l.current_ma,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)