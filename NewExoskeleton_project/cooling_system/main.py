# cooling_system/main.py
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Float, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 7005))
MODULE_NAME = os.getenv('MODULE_NAME', 'cooling_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///cooling_system.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class CoolingLogDB(Base):
    __tablename__ = 'cooling_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    active = Column(Boolean)
    fan_speed = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


@dataclass
class CoolingSystem:
    active: bool = False
    fan_speed: float = 0.0
    max_speed: float = 1.0
    log: list = field(default_factory=list)

    def set_speed(self, speed: float) -> None:
        speed = max(0.0, min(speed, self.max_speed))
        self.fan_speed = speed
        self.active = speed > 0
        msg = (
            f"Охлаждение: {'ВКЛ' if self.active else 'ВЫКЛ'}, "
            f"скорость={speed:.2f}"
        )
        self.log.append(msg)
        logger.info(msg)

    def off(self) -> None:
        self.set_speed(0.0)

    def snapshot(self) -> dict:
        return {
            'service': 'cooling',
            'active': self.active,
            'fan_speed': self.fan_speed,
            'log_tail': self.log[-8:]
        }


_mod = CoolingSystem()


def save_log():
    session = SessionLocal()
    try:
        session.add(CoolingLogDB(
            active=_mod.active,
            fan_speed=_mod.fan_speed
        ))
        session.commit()
    finally:
        session.close()


class SpeedRequest(BaseModel):
    speed: float = 0.0


app = FastAPI(title="Cooling System", version="2.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def status():
    return _mod.snapshot()


@app.post('/speed')
def set_speed(body: SpeedRequest):
    _mod.set_speed(body.speed)
    save_log()
    return {'ok': True, 'state': _mod.snapshot()}


@app.post('/off')
def off():
    _mod.off()
    save_log()
    return {'ok': True, 'state': _mod.snapshot()}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(CoolingLogDB)
            .order_by(CoolingLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id, 'active': l.active,
            'fan_speed': l.fan_speed,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)