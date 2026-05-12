# stop_module/main.py
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 7001))
MODULE_NAME = os.getenv('MODULE_NAME', 'stop_module')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///stop_module.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class StopReason(str, Enum):
    PATIENT_ESTOP = "patient_emergency"
    DOCTOR_ESTOP = "doctor_emergency"
    MONITORING_OBSTACLE = "monitoring_obstacle"
    UNAUTHORIZED_COMMAND = "unauthorized_command"
    LOSS_OF_BALANCE = "loss_of_balance"
    MANUAL_RESET = "manual_reset"


class StopEventDB(Base):
    __tablename__ = 'stop_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50))
    reason = Column(String(50), nullable=True)
    drives_enabled = Column(Boolean)
    stopped = Column(Boolean)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


@dataclass
class StopModule:
    drives_enabled: bool = False
    stopped: bool = False
    last_reason: Optional[StopReason] = None
    last_event_at: Optional[datetime] = None
    log: list = field(default_factory=list)

    def emergency_stop(self, reason: StopReason) -> None:
        self.drives_enabled = False
        self.stopped = True
        self.last_reason = reason
        self.last_event_at = datetime.now(timezone.utc)
        self._log(f"АВАРИЙНАЯ ОСТАНОВКА: {reason.value}")
        logger.warning(f"EMERGENCY STOP: {reason.value}")

    def smooth_stop(self) -> None:
        self.drives_enabled = False
        self.stopped = False
        self.last_reason = None
        self.last_event_at = datetime.now(timezone.utc)
        self._log("Плавная остановка")
        logger.info("Smooth stop executed")

    def allow_movement(self) -> bool:
        if self.stopped:
            self._log("Приводы не включены: активна аварийная остановка")
            return False
        self.drives_enabled = True
        self.last_event_at = datetime.now(timezone.utc)
        self._log("Приводы разрешены")
        logger.info("Movement allowed")
        return True

    def reset_from_emergency(self, authorized: bool) -> bool:
        if not authorized:
            self._log("Отказ сброса: нет полномочий")
            return False
        self.stopped = False
        self.last_reason = StopReason.MANUAL_RESET
        self.last_event_at = datetime.now(timezone.utc)
        self._log("Аварийный режим сброшен")
        logger.info("Emergency reset authorized")
        return True

    def snapshot(self) -> dict:
        return {
            'service': 'stop',
            'drives_enabled': self.drives_enabled,
            'stopped': self.stopped,
            'last_reason': self.last_reason.value if self.last_reason else None,
            'last_event_at': (
                self.last_event_at.isoformat() if self.last_event_at else None
            ),
            'log_tail': self.log[-8:]
        }

    def _log(self, message: str) -> None:
        self.log.append(message)


_mod = StopModule()


def save_event(event_type: str, reason: str = None):
    session = SessionLocal()
    try:
        session.add(StopEventDB(
            event_type=event_type,
            reason=reason,
            drives_enabled=_mod.drives_enabled,
            stopped=_mod.stopped
        ))
        session.commit()
    finally:
        session.close()


# --- Pydantic ---
class EmergencyStopRequest(BaseModel):
    reason: str = "patient_emergency"


class ResetRequest(BaseModel):
    authorized: bool = False


app = FastAPI(title="Stop Module", version="2.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def status():
    return _mod.snapshot()


@app.post('/emergency-stop')
def emergency_stop(body: EmergencyStopRequest):
    reason = StopReason(body.reason)
    _mod.emergency_stop(reason)
    save_event('emergency_stop', reason.value)
    return {'ok': True, 'state': _mod.snapshot()}


@app.post('/smooth-stop')
def smooth_stop():
    _mod.smooth_stop()
    save_event('smooth_stop')
    return {'ok': True, 'state': _mod.snapshot()}


@app.post('/allow-movement')
def allow_movement():
    ok = _mod.allow_movement()
    save_event('allow_movement')
    return {'ok': ok, 'state': _mod.snapshot()}


@app.post('/reset-emergency')
def reset_emergency(body: ResetRequest):
    ok = _mod.reset_from_emergency(body.authorized)
    save_event('reset_emergency')
    return {'ok': ok, 'state': _mod.snapshot()}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        events = (
            session.query(StopEventDB)
            .order_by(StopEventDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': e.id, 'event_type': e.event_type,
            'reason': e.reason,
            'drives_enabled': e.drives_enabled,
            'stopped': e.stopped,
            'created_at': e.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if e.created_at else None
        } for e in events]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)