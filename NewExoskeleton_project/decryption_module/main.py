# decryption_module/main.py
import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5103))
MODULE_NAME = os.getenv('MODULE_NAME', 'decryption_module')
CRYPTO_URL = os.getenv('CRYPTO_URL', 'http://localhost:5102')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///decryption_module.db')
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class DecryptionLogDB(Base):
    __tablename__ = 'decryption_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    doctor_id = Column(String(100))
    source_module = Column(String(100))
    success = Column(Boolean)
    verified = Column(Boolean)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(engine)

active_sessions: dict = {}


class SessionInitRequest(BaseModel):
    doctor_id: str
    session_token: str


class DecryptPacketRequest(BaseModel):
    doctor_id: str
    session_token: str
    ciphertext: str
    signature: str
    source: str = 'comms_module'
    target: str = 'doctor_client'


def save_log(
    doctor_id: str,
    source_module: str,
    success: bool,
    verified: bool,
    error: Optional[str] = None
):
    session = SessionLocal()
    try:
        session.add(DecryptionLogDB(
            doctor_id=doctor_id,
            source_module=source_module,
            success=success,
            verified=verified,
            error=error
        ))
        session.commit()
    finally:
        session.close()


app = FastAPI(title="Decryption Module", version="1.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def status():
    return {
        'service': MODULE_NAME,
        'active_sessions': list(active_sessions.keys()),
        'total_sessions': len(active_sessions)
    }


@app.post('/session/init')
def session_init(body: SessionInitRequest):
    active_sessions[body.doctor_id] = body.session_token
    logger.info(f"Decrypt session initialized for doctor={body.doctor_id}")
    return {
        'ok': True,
        'doctor_id': body.doctor_id,
        'session_active': True
    }


@app.post('/decrypt_packet')
def decrypt_packet(body: DecryptPacketRequest):
    expected = active_sessions.get(body.doctor_id)
    if not expected:
        save_log(body.doctor_id, body.source, False, False,
                 'session_not_initialized')
        raise HTTPException(
            status_code=403, detail='Session not initialized'
        )

    if expected != body.session_token:
        save_log(body.doctor_id, body.source, False, False,
                 'session_token_mismatch')
        raise HTTPException(
            status_code=403, detail='Session token mismatch'
        )

    try:
        with httpx.Client(timeout=5.0) as c:
            resp = c.post(
                f'{CRYPTO_URL}/decrypt',
                json={
                    'ciphertext': body.ciphertext,
                    'source': body.source,
                    'target': body.target,
                    'signature': body.signature
                }
            )
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        save_log(body.doctor_id, body.source, False, False, str(e))
        raise HTTPException(status_code=502, detail=str(e))

    plaintext = result.get('plaintext', '')
    try:
        parsed = json.loads(plaintext)
    except Exception:
        parsed = plaintext

    save_log(
        body.doctor_id, body.source,
        True, bool(result.get('verified', False))
    )

    return {
        'ok': True,
        'doctor_id': body.doctor_id,
        'verified': result.get('verified', False),
        'data': parsed
    }


@app.post('/session/close')
def close_session(doctor_id: str):
    active_sessions.pop(doctor_id, None)
    logger.info(f"Session closed for doctor={doctor_id}")
    return {
        'ok': True,
        'doctor_id': doctor_id,
        'session_active': False
    }


@app.get('/history')
def history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        rows = (
            session.query(DecryptionLogDB)
            .order_by(DecryptionLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': r.id,
            'doctor_id': r.doctor_id,
            'source_module': r.source_module,
            'success': r.success,
            'verified': r.verified,
            'error': r.error,
            'created_at': r.created_at.isoformat()
            if r.created_at else None
        } for r in rows]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)