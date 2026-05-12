# carriage_system/main.py
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 7002))
MODULE_NAME = os.getenv('MODULE_NAME', 'carriage_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///carriage_system.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class CarriageState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    MOVING = "moving"


class CarriageEventDB(Base):
    __tablename__ = 'carriage_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50))
    state_before = Column(String(20))
    state_after = Column(String(20))
    emergency = Column(String(10))
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


@dataclass
class CarriageSystem:
    state: CarriageState = CarriageState.CLOSED
    log: list = field(default_factory=list)

    def request_open(
        self, *, drives_stopped: bool, emergency: bool = False
    ) -> bool:
        if not emergency and not drives_stopped:
            self._log("Отказ: приводы активны, открытие небезопасно")
            logger.warning("Open refused: drives still active")
            return False
        self.state = CarriageState.OPEN
        msg = "Корпус открыт" + (" (аварийное)" if emergency else "")
        self._log(msg)
        logger.info(msg)
        return True

    def request_close(self) -> bool:
        if self.state == CarriageState.MOVING:
            self._log("Отказ: механизм в движении")
            return False
        self.state = CarriageState.CLOSED
        self._log("Корпус закрыт, пациент зафиксирован")
        logger.info("Carriage closed")
        return True

    def snapshot(self) -> dict:
        return {
            'service': 'carriage',
            'state': self.state.value,
            'log_tail': self.log[-8:]
        }

    def _log(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {message}")


_mod = CarriageSystem()


def save_event(
    event_type: str, state_before: str,
    state_after: str, emergency: bool = False
):
    session = SessionLocal()
    try:
        session.add(CarriageEventDB(
            event_type=event_type,
            state_before=state_before,
            state_after=state_after,
            emergency=str(emergency)
        ))
        session.commit()
    finally:
        session.close()


class OpenRequest(BaseModel):
    drives_stopped: bool = True
    emergency: bool = False


app = FastAPI(title="Carriage System", version="2.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def status():
    return _mod.snapshot()


@app.post('/open')
def open_carriage(body: OpenRequest):
    state_before = _mod.state.value
    ok = _mod.request_open(
        drives_stopped=body.drives_stopped,
        emergency=body.emergency
    )
    save_event('open', state_before, _mod.state.value, body.emergency)
    return {'ok': ok, 'state': _mod.snapshot()}


@app.post('/close')
def close_carriage():
    state_before = _mod.state.value
    ok = _mod.request_close()
    save_event('close', state_before, _mod.state.value)
    return {'ok': ok, 'state': _mod.snapshot()}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        events = (
            session.query(CarriageEventDB)
            .order_by(CarriageEventDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': e.id, 'event_type': e.event_type,
            'state_before': e.state_before,
            'state_after': e.state_after,
            'emergency': e.emergency,
            'created_at': e.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if e.created_at else None
        } for e in events]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)