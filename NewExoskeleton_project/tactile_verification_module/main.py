# tactile_verification_module/main.py
import os
import logging
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float,
    String, Boolean, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST        = '0.0.0.0'
PORT        = int(os.getenv('PORT', 5004))
MODULE_NAME = os.getenv('MODULE_NAME', 'tactile_verification_module')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

TACTILE_URL     = os.getenv('TACTILE_URL', 'http://localhost:7006')
REQUEST_TIMEOUT = 5.0

MAX_ALLOWED_INTENSITY = 0.4
SCALE_FACTOR          = 0.4

DATABASE_URL = 'sqlite:///tactile_verification.db'
engine       = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()


class TactileVerificationLogDB(Base):
    __tablename__ = 'tactile_verification_log'

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    pattern             = Column(String(50))
    requested_intensity = Column(Float)
    limited_intensity   = Column(Float)
    was_limited         = Column(Boolean)
    source_trusted      = Column(Boolean)
    forwarded           = Column(Boolean, default=False)
    tactile_response    = Column(String(200), nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

stats = {
    'total_requests':   0,
    'limited_count':    0,
    'forwarded_count':  0,
}


class TactileEmitRequest(BaseModel):
    pattern:        str   = 'contact_sole'
    intensity:      float = 0.5
    source_trusted: bool  = False


# ── Вспомогательные функции ───────────────────────────────────────────────────

def get_client() -> httpx.Client:
    """Фабрика httpx.Client — патчится в тестах."""
    return httpx.Client(timeout=REQUEST_TIMEOUT)


def limit_intensity(requested: float) -> tuple[float, bool]:
    """
    Ограничивает интенсивность:
      scaled  = requested × SCALE_FACTOR
      limited = min(scaled, MAX_ALLOWED_INTENSITY)
    """
    scaled    = requested * SCALE_FACTOR
    limited   = min(scaled, MAX_ALLOWED_INTENSITY)
    was_limited = limited < requested
    return round(limited, 4), was_limited


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Tactile Verification Module", version="1.1")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def get_status():
    return {
        'service':               MODULE_NAME,
        'max_allowed_intensity': MAX_ALLOWED_INTENSITY,
        'scale_factor':          SCALE_FACTOR,
        'stats':                 stats,
    }


@app.post('/emit')
def verified_emit(body: TactileEmitRequest):
    """
    Верифицированная отправка тактильного сигнала.
    Ограничивает интенсивность перед передачей в tactile_system.
    """
    stats['total_requests'] += 1

    limited, was_limited = limit_intensity(body.intensity)

    if was_limited:
        stats['limited_count'] += 1
        logger.info(
            f"Intensity limited: {body.intensity} → {limited} "
            f"(pattern={body.pattern})"
        )
    else:
        logger.info(
            f"Intensity accepted: {limited} (pattern={body.pattern})"
        )

    # Передаём в tactile_system с ограниченной интенсивностью
    tactile_response = None
    forwarded        = False
    try:
        with get_client() as c:
            resp = c.post(
                f'{TACTILE_URL}/emit',
                json={
                    'pattern':        body.pattern,
                    'intensity':      limited,
                    'source_trusted': body.source_trusted,
                }
            )
            tactile_response = str(resp.json())
            forwarded        = resp.status_code == 200
            stats['forwarded_count'] += 1
    except Exception as e:
        logger.error(f"Tactile forward failed: {e}")
        tactile_response = f"error: {e}"

    session = SessionLocal()
    try:
        session.add(TactileVerificationLogDB(
            pattern=body.pattern,
            requested_intensity=body.intensity,
            limited_intensity=limited,
            was_limited=was_limited,
            source_trusted=body.source_trusted,
            forwarded=forwarded,
            tactile_response=tactile_response,
        ))
        session.commit()
    finally:
        session.close()

    return {
        'ok':                 True,
        'pattern':            body.pattern,
        'requested_intensity': body.intensity,
        'limited_intensity':  limited,
        'was_limited':        was_limited,
        'max_allowed':        MAX_ALLOWED_INTENSITY,
        'forwarded':          forwarded,
        'tactile_response':   tactile_response,
    }


@app.get('/limits')
def get_limits():
    return {
        'max_allowed_intensity': MAX_ALLOWED_INTENSITY,
        'scale_factor':          SCALE_FACTOR,
        'description': (
            'Input intensity is multiplied by scale_factor, '
            'then capped at max_allowed_intensity. '
            f'Even at input=1.0, patient receives at most '
            f'{MAX_ALLOWED_INTENSITY}.'
        ),
    }


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(TactileVerificationLogDB)
            .order_by(TactileVerificationLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id':                   l.id,
            'pattern':              l.pattern,
            'requested_intensity':  l.requested_intensity,
            'limited_intensity':    l.limited_intensity,
            'was_limited':          l.was_limited,
            'source_trusted':       l.source_trusted,
            'forwarded':            l.forwarded,
            'tactile_response':     l.tactile_response,
            'created_at':           l.created_at.strftime(
                '%Y-%m-%d %H:%M:%S'
            ) if l.created_at else None,
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)