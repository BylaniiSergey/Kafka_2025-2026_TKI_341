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
    create_engine, Column, Integer, String,
    Boolean, DateTime, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST        = '0.0.0.0'
PORT        = int(os.getenv('PORT', 5103))
MODULE_NAME = os.getenv('MODULE_NAME', 'decryption_module')

CRYPTO_URL = os.getenv('CRYPTO_URL',  'http://localhost:4001')
COMMS_URL  = os.getenv('COMMS_URL',   'http://localhost:6001')

REQUEST_TIMEOUT = 5.0

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = os.getenv(
    'DATABASE_URL', 'sqlite:///decryption_module.db'
)
engine       = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()


class DecryptionLogDB(Base):
    __tablename__ = 'decryption_log'
    id            = Column(Integer, primary_key=True, autoincrement=True)
    doctor_id     = Column(String(100))
    source_module = Column(String(100))
    packet_type   = Column(String(50),  nullable=True)
    success       = Column(Boolean)
    verified      = Column(Boolean)
    error         = Column(Text, nullable=True)
    created_at    = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc)
    )


Base.metadata.create_all(engine)

# Активные сессии врачей: doctor_id → session_token
active_sessions: dict[str, str] = {}


# ── Pydantic модели ───────────────────────────────────────────────────────────

class SessionInitRequest(BaseModel):
    doctor_id:     str
    session_token: str


class DecryptPacketRequest(BaseModel):
    """Врач передаёт зашифрованный пакет для расшифровки."""
    doctor_id:     str
    session_token: str
    ciphertext:    str
    signature:     str
    source:        str = 'control_system'
    target:        str = 'comms_module'


class DecryptLatestRequest(BaseModel):
    """Врач запрашивает расшифровку последнего пакета из comms_module."""
    doctor_id:     str
    session_token: str


# ── HTTP-клиент (патчится в тестах) ──────────────────────────────────────────

def get_client() -> httpx.Client:
    """
    Фабрика HTTP-клиента.
    Патчится в тестах через patch.object(mod, 'get_client', ...).
    """
    return httpx.Client(timeout=REQUEST_TIMEOUT)


# ── Вспомогательные функции ───────────────────────────────────────────────────

def save_log(
    doctor_id:     str,
    source_module: str,
    success:       bool,
    verified:      bool,
    packet_type:   str  = None,
    error:         str  = None,
):
    session = SessionLocal()
    try:
        session.add(DecryptionLogDB(
            doctor_id=doctor_id,
            source_module=source_module,
            packet_type=packet_type,
            success=success,
            verified=verified,
            error=error,
        ))
        session.commit()
    finally:
        session.close()


def _verify_session(doctor_id: str, session_token: str) -> bool:
    expected = active_sessions.get(doctor_id)
    if not expected:
        return False
    return expected == session_token


def _call_crypto_decrypt(
    ciphertext: str,
    signature:  str,
    source:     str,
    target:     str,
) -> dict:
    """
    Вызывает crypto_module/decrypt для расшифровки пакета.
    Использует get_client() — патчится в тестах.
    Возвращает {'plaintext': ..., 'verified': bool}.
    """
    with get_client() as c:
        resp = c.post(f'{CRYPTO_URL}/decrypt', json={
            'ciphertext': ciphertext,
            'source':     source,
            'target':     target,
            'signature':  signature,
        })
        resp.raise_for_status()
        return resp.json()


def _fetch_latest_from_comms() -> dict:
    """
    Получает последний зашифрованный пакет из comms_module.
    Использует get_client() — патчится в тестах.
    """
    with get_client() as c:
        resp = c.get(f'{COMMS_URL}/latest_encrypted_packet')
        if resp.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail='No encrypted packets in comms_module'
            )
        resp.raise_for_status()
        return resp.json()


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Decryption Module", version="2.1")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def status():
    return {
        'service':         MODULE_NAME,
        'active_sessions': list(active_sessions.keys()),
        'total_sessions':  len(active_sessions),
    }


# ── Управление сессиями врачей ────────────────────────────────────────────────

@app.post('/session/init')
def session_init(body: SessionInitRequest):
    """Инициализирует защищённую сессию для врача."""
    active_sessions[body.doctor_id] = body.session_token
    logger.info(
        f"Decrypt session initialized for doctor={body.doctor_id}"
    )
    return {
        'ok':             True,
        'doctor_id':      body.doctor_id,
        'session_active': True,
    }


@app.post('/session/close')
def close_session(doctor_id: str):
    """Закрывает сессию врача."""
    active_sessions.pop(doctor_id, None)
    logger.info(f"Session closed for doctor={doctor_id}")
    return {
        'ok':             True,
        'doctor_id':      doctor_id,
        'session_active': False,
    }


# ── Расшифровка пакетов ───────────────────────────────────────────────────────

@app.post('/decrypt_packet')
def decrypt_packet(body: DecryptPacketRequest):
    """
    Расшифровывает пакет для врача.

    Цепочка:
      control_system → crypto_module (encrypt) → comms_module (store)
      → decryption_module (decrypt) → врач

    1. Проверяет сессию врача
    2. Вызывает crypto_module/decrypt через get_client()
    3. Верифицирует подпись
    4. Возвращает расшифрованные данные врачу
    """
    # Шаг 1: Проверка сессии
    if not _verify_session(body.doctor_id, body.session_token):
        save_log(
            body.doctor_id, body.source, False, False,
            error='session_not_initialized_or_mismatch'
        )
        raise HTTPException(
            status_code=403,
            detail='Session not initialized or token mismatch'
        )

    # Шаг 2: Расшифровка через crypto_module
    try:
        result = _call_crypto_decrypt(
            ciphertext=body.ciphertext,
            signature=body.signature,
            source=body.source,
            target=body.target,
        )
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        error_msg = (
            "Signature verification failed"
            if e.response.status_code == 403
            else f"Crypto error: {e}"
        )
        save_log(
            body.doctor_id, body.source, False, False,
            error=error_msg
        )
        raise HTTPException(
            status_code=e.response.status_code,
            detail=error_msg
        )
    except Exception as e:
        save_log(
            body.doctor_id, body.source, False, False,
            error=str(e)
        )
        raise HTTPException(status_code=502, detail=str(e))

    # Шаг 3: Парсинг расшифрованных данных
    plaintext = result.get('plaintext', '')
    verified  = bool(result.get('verified', False))

    try:
        parsed_data = json.loads(plaintext)
    except Exception:
        parsed_data = plaintext

    # Определяем тип пакета
    packet_type = None
    if isinstance(parsed_data, dict):
        packet_type = parsed_data.get('type') or (
            'alarm'     if 'alarms'    in parsed_data else
            'telemetry' if 'telemetry' in parsed_data else
            'data'
        )

    save_log(
        body.doctor_id, body.source,
        True, verified, packet_type=packet_type
    )

    logger.info(
        f"Decrypted for doctor={body.doctor_id}: "
        f"type={packet_type}, verified={verified}"
    )

    return {
        'ok':          True,
        'doctor_id':   body.doctor_id,
        'verified':    verified,
        'packet_type': packet_type,
        'data':        parsed_data,
    }


@app.post('/decrypt_latest')
def decrypt_latest_from_comms(body: DecryptLatestRequest):
    """
    Удобный эндпоинт: врач запрашивает последний пакет из comms_module
    и сразу получает расшифрованные данные.

    Цепочка:
      decryption_module → comms_module (get latest) →
      crypto_module (decrypt) → врач
    """
    # Шаг 1: Проверка сессии
    if not _verify_session(body.doctor_id, body.session_token):
        raise HTTPException(
            status_code=403,
            detail='Session not initialized or token mismatch'
        )

    # Шаг 2: Получить последний пакет из comms_module
    try:
        comms_data = _fetch_latest_from_comms()
    except HTTPException:
        raise
    except Exception as e:
        save_log(
            body.doctor_id, 'comms_module', False, False,
            error=str(e)
        )
        raise HTTPException(status_code=502, detail=str(e))

    packet     = comms_data.get('packet', {})
    ciphertext = packet.get('ciphertext', '')
    signature  = packet.get('signature',  '')
    source     = packet.get('source',     'control_system')
    target     = packet.get('target',     'comms_module')

    if not ciphertext or not signature:
        raise HTTPException(
            status_code=400,
            detail='Invalid packet from comms_module'
        )

    # Шаг 3: Расшифровать через crypto_module
    try:
        result = _call_crypto_decrypt(
            ciphertext=ciphertext,
            signature=signature,
            source=source,
            target=target,
        )
    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        save_log(
            body.doctor_id, source, False, False,
            error=f"Crypto error: {e}"
        )
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Crypto error: {e}"
        )
    except Exception as e:
        save_log(
            body.doctor_id, source, False, False,
            error=str(e)
        )
        raise HTTPException(status_code=502, detail=str(e))

    plaintext = result.get('plaintext', '')
    verified  = bool(result.get('verified', False))

    try:
        parsed_data = json.loads(plaintext)
    except Exception:
        parsed_data = plaintext

    packet_type = packet.get('type', 'unknown')

    save_log(
        body.doctor_id, source,
        True, verified, packet_type=packet_type
    )

    logger.info(
        f"Decrypt_latest for doctor={body.doctor_id}: "
        f"type={packet_type}, verified={verified}"
    )

    return {
        'ok':          True,
        'doctor_id':   body.doctor_id,
        'verified':    verified,
        'packet_type': packet_type,
        'received_at': packet.get('received_at'),
        'timestamp':   packet.get('timestamp'),
        'data':        parsed_data,
    }


# ── История ───────────────────────────────────────────────────────────────────

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
            'id':            r.id,
            'doctor_id':     r.doctor_id,
            'source_module': r.source_module,
            'packet_type':   r.packet_type,
            'success':       r.success,
            'verified':      r.verified,
            'error':         r.error,
            'created_at':    r.created_at.isoformat()
                             if r.created_at else None,
        } for r in rows]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)