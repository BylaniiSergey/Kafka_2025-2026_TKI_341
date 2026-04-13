# tactile_system/main.py
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Float, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 7006))
MODULE_NAME = os.getenv('MODULE_NAME', 'tactile_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///tactile_system.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class TactilePattern(str, Enum):
    CONTACT_SOLE = "contact_sole"
    WARNING = "warning"
    CUSTOM = "custom"


class TactileLogDB(Base):
    __tablename__ = 'tactile_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    pattern = Column(String(50))
    intensity = Column(Float)
    source_trusted = Column(Boolean)
    message = Column(String(200))
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


@dataclass
class TactileModule:
    max_intensity: float = 0.85
    last_output: Optional[str] = None
    history: list = field(default_factory=list)

    def emit(
        self,
        pattern: TactilePattern,
        intensity: float,
        *,
        source_trusted: bool
    ) -> Optional[str]:
        if not source_trusted:
            self._remember("Отказ: источник сигнала не доверен")
            return None
        clamped = max(0.0, min(float(intensity), self.max_intensity))
        if clamped != intensity:
            self._remember(
                f"Интенсивность ограничена: {intensity} → {clamped}"
            )
        msg = f"{pattern.value}, интенсивность={clamped:.2f}"
        self.last_output = msg
        self._remember(f"Сигнал: {msg}")
        logger.info(f"Tactile emit: {msg}")
        return msg

    def snapshot(self) -> dict:
        return {
            'service': 'tactile',
            'last_output': self.last_output,
            'history_tail': self.history[-8:]
        }

    def _remember(self, line: str) -> None:
        self.history.append(line)


_mod = TactileModule()


def save_log(
    pattern: str, intensity: float,
    source_trusted: bool, message: str
):
    session = SessionLocal()
    try:
        session.add(TactileLogDB(
            pattern=pattern, intensity=intensity,
            source_trusted=source_trusted,
            message=message or ''
        ))
        session.commit()
    finally:
        session.close()


class EmitRequest(BaseModel):
    pattern: str = "contact_sole"
    intensity: float = 0.5
    source_trusted: bool = False


app = FastAPI(title="Tactile System", version="2.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def status():
    return _mod.snapshot()


@app.post('/emit')
def emit(body: EmitRequest):
    pattern = TactilePattern(body.pattern)
    msg = _mod.emit(
        pattern, body.intensity,
        source_trusted=body.source_trusted
    )
    save_log(
        body.pattern, body.intensity,
        body.source_trusted, msg or ''
    )
    return {'ok': True, 'message': msg, 'state': _mod.snapshot()}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(TactileLogDB)
            .order_by(TactileLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id, 'pattern': l.pattern,
            'intensity': l.intensity,
            'source_trusted': l.source_trusted,
            'message': l.message,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)