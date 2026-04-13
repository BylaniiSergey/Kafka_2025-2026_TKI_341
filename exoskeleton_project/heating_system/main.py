# heating_system/main.py
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
PORT = int(os.getenv('PORT', 7004))
MODULE_NAME = os.getenv('MODULE_NAME', 'heating_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///heating_system.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class HeatingLogDB(Base):
    __tablename__ = 'heating_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    active = Column(Boolean)
    power_level = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


@dataclass
class HeatingSystem:
    active: bool = False
    power_level: float = 0.0
    max_power: float = 1.0
    log: list = field(default_factory=list)

    def set_level(self, level: float) -> None:
        level = max(0.0, min(level, self.max_power))
        self.power_level = level
        self.active = level > 0
        msg = f"Нагрев: {'ВКЛ' if self.active else 'ВЫКЛ'}, мощность={level:.2f}"
        self.log.append(msg)
        logger.info(msg)

    def off(self) -> None:
        self.set_level(0.0)

    def snapshot(self) -> dict:
        return {
            'service': 'heating',
            'active': self.active,
            'power_level': self.power_level,
            'log_tail': self.log[-8:]
        }


_mod = HeatingSystem()


def save_log():
    session = SessionLocal()
    try:
        session.add(HeatingLogDB(
            active=_mod.active,
            power_level=_mod.power_level
        ))
        session.commit()
    finally:
        session.close()


class LevelRequest(BaseModel):
    level: float = 0.0


app = FastAPI(title="Heating System", version="2.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def status():
    return _mod.snapshot()


@app.post('/level')
def set_level(body: LevelRequest):
    _mod.set_level(body.level)
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
            session.query(HeatingLogDB)
            .order_by(HeatingLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id, 'active': l.active,
            'power_level': l.power_level,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)