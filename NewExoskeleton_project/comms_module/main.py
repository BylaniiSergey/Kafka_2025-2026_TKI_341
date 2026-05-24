# comms_module/main.py
import os
import logging
import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String,
    Boolean, DateTime, Text, func, inspect, text
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST        = '0.0.0.0'
PORT        = int(os.getenv('PORT', 6001))
MODULE_NAME = os.getenv('MODULE_NAME', 'comms_module')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

MONITORING_URL  = os.getenv('MONITORING_URL',  'http://localhost:6002')
SENSORS_URL     = os.getenv('SENSORS_URL',     'http://localhost:6003')
REQUEST_TIMEOUT = 5.0

DATABASE_URL = 'sqlite:///comms_module.db'
engine       = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()


class CommsLogDB(Base):
    __tablename__ = 'comms_log'

    id              = Column(Integer, primary_key=True, autoincrement=True)
    event_type      = Column(String(50))
    source          = Column(String(50),  nullable=True)
    data            = Column(Text,        nullable=True)
    encrypted       = Column(Boolean,     default=False)
    sent_to_doctors = Column(Boolean,     default=False)
    created_at      = Column(DateTime,    server_default=func.now())


def _migrate_db():
    """
    Добавляет недостающие колонки в существующую таблицу.
    Безопасно — не трогает данные, не пересоздаёт таблицу.
    """
    try:
        insp = inspect(engine)
        if not insp.has_table('comms_log'):
            return  # create_all создаст таблицу ниже

        existing = {col['name'] for col in insp.get_columns('comms_log')}
        needed = {
            'encrypted':       'BOOLEAN DEFAULT 0',
            'sent_to_doctors': 'BOOLEAN DEFAULT 0',
            'created_at':      'DATETIME',
            'source':          'VARCHAR(50)',
            'data':            'TEXT',
            'event_type':      'VARCHAR(50)',
        }
        with engine.connect() as conn:
            for col_name, col_def in needed.items():
                if col_name not in existing:
                    conn.execute(
                        text(f'ALTER TABLE comms_log ADD COLUMN {col_name} {col_def}')
                    )
                    logger.info(f"DB migration: added column '{col_name}'")
            conn.commit()
    except Exception as e:
        logger.error(f"DB migration error: {e}")


_migrate_db()
Base.metadata.create_all(engine)

# Активные WebSocket-соединения врачей
active_connections: Dict[str, WebSocket] = {}

# Хранилище зашифрованных пакетов
_pending_encrypted_packets = []


# ── HTTP-клиент (патчится в тестах) ──────────────────────────────────────────

def get_client() -> httpx.Client:
    return httpx.Client(timeout=REQUEST_TIMEOUT)


def get_async_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT)


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_log(
    event_type: str,
    source:     str  = None,
    data:       str  = None,
    encrypted:  bool = False,
    sent:       bool = False,
):
    session = SessionLocal()
    try:
        session.add(CommsLogDB(
            event_type=event_type,
            source=source,
            data=data,
            encrypted=encrypted,
            sent_to_doctors=sent,
        ))
        session.commit()
    except Exception as e:
        logger.error(f"save_log error: {e}")
        session.rollback()
    finally:
        session.close()


# ── Pydantic модели ───────────────────────────────────────────────────────────

class AlarmRequest(BaseModel):
    alarms: list


class EncryptedPacket(BaseModel):
    ciphertext:   str
    signature:    str
    source:       str           = 'control_system'
    target:       str           = 'comms_module'
    timestamp:    Optional[str] = None
    alarms_count: Optional[int] = None


class CommandRequest(BaseModel):
    type:   str
    source: str  = 'doctor'
    data:   dict = {}


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Comms Module", version="3.3")


# ── Health / Status ───────────────────────────────────────────────────────────

@app.get('/health')
def health():
    return {
        'status':             'ok',
        'service':            MODULE_NAME,
        'active_connections': len(active_connections),
        'pending_packets':    len(_pending_encrypted_packets),
    }


@app.get('/status')
def status():
    return {
        'service':            MODULE_NAME,
        'active_connections': list(active_connections.keys()),
        'total_connections':  len(active_connections),
        'pending_packets':    len(_pending_encrypted_packets),
    }


# ── WebSocket для врачей ──────────────────────────────────────────────────────

@app.websocket('/doctor/{doctor_id}')
async def doctor_endpoint(websocket: WebSocket, doctor_id: str):
    await websocket.accept()
    conn_key = f"doctor_{doctor_id}"
    active_connections[conn_key] = websocket
    logger.info(f"Doctor {doctor_id} connected via WebSocket")
    save_log('ws_connect', source=doctor_id)

    try:
        while True:
            try:
                if _pending_encrypted_packets:
                    packet = _pending_encrypted_packets[-1]
                    await websocket.send_json({
                        'type':      'encrypted_telemetry',
                        'encrypted': True,
                        'packet':    packet,
                        'note':      'Use decryption_module to decrypt',
                    })
                else:
                    await websocket.send_json({
                        'type':      'status',
                        'encrypted': False,
                        'message':   'No encrypted packets available',
                    })
            except Exception as e:
                logger.error(f"WS loop error: {e}")

            await asyncio.sleep(2)

    except WebSocketDisconnect:
        logger.info(f"Doctor {doctor_id} disconnected")
        save_log('ws_disconnect', source=doctor_id)
    finally:
        active_connections.pop(conn_key, None)


# ── Приём зашифрованных пакетов ───────────────────────────────────────────────

@app.post('/alarm_encrypted')
async def receive_encrypted_alarm(body: EncryptedPacket):
    try:
        logger.warning(
            f"Encrypted alarm received from {body.source}: "
            f"alarms_count={body.alarms_count}, "
            f"sig={body.signature[:8]}..."
        )

        now = _now_iso()
        packet = {
            'type':        'alarm',
            'ciphertext':  body.ciphertext,
            'signature':   body.signature,
            'source':      body.source,
            'target':      body.target,
            'timestamp':   body.timestamp or now,
            'received_at': now,
        }

        _pending_encrypted_packets.append(packet)

        save_log(
            'alarm_encrypted',
            source=body.source,
            data=f"sig={body.signature[:16]}...",
            encrypted=True,
        )

        sent_count = 0
        if active_connections:
            for conn_key, websocket in list(active_connections.items()):
                try:
                    await websocket.send_json({
                        'type':      'encrypted_alarm',
                        'encrypted': True,
                        'packet':    packet,
                        'note':      'Use decryption_module/decrypt_packet',
                    })
                    sent_count += 1
                except Exception as e:
                    logger.error(f"Failed to send alarm to {conn_key}: {e}")

        save_log('alarm_encrypted_sent', encrypted=True, sent=True)

        return {
            'status':    'encrypted_alarm_received',
            'sent_to':   sent_count,
            'encrypted': True,
            'signature': body.signature[:16] + '...',
        }
    except Exception as e:
        logger.error(f"/alarm_encrypted error: {e}", exc_info=True)
        raise


@app.post('/telemetry_encrypted')
async def receive_encrypted_telemetry(body: EncryptedPacket):
    try:
        logger.info(
            f"Encrypted telemetry received from {body.source}: "
            f"sig={body.signature[:8]}..."
        )

        now = _now_iso()
        packet = {
            'type':        'telemetry',
            'ciphertext':  body.ciphertext,
            'signature':   body.signature,
            'source':      body.source,
            'target':      body.target,
            'timestamp':   body.timestamp or now,
            'received_at': now,
        }

        _pending_encrypted_packets.append(packet)

        if len(_pending_encrypted_packets) > 100:
            _pending_encrypted_packets.pop(0)

        save_log(
            'telemetry_encrypted',
            source=body.source,
            data=f"sig={body.signature[:16]}...",
            encrypted=True,
        )

        sent_count = 0
        if active_connections:
            for conn_key, websocket in list(active_connections.items()):
                try:
                    await websocket.send_json({
                        'type':      'encrypted_telemetry',
                        'encrypted': True,
                        'packet':    packet,
                        'note':      'Use decryption_module/decrypt_packet',
                    })
                    sent_count += 1
                except Exception as e:
                    logger.error(f"Failed to send telemetry to {conn_key}: {e}")

        return {
            'status':    'encrypted_telemetry_received',
            'sent_to':   sent_count,
            'encrypted': True,
        }
    except Exception as e:
        logger.error(f"/telemetry_encrypted error: {e}", exc_info=True)
        raise


@app.post('/command_encrypted')
async def receive_encrypted_command(body: EncryptedPacket):
    try:
        logger.info(
            f"Encrypted command received from {body.source}: "
            f"sig={body.signature[:8]}..."
        )

        now = _now_iso()
        packet = {
            'type':        'command',
            'ciphertext':  body.ciphertext,
            'signature':   body.signature,
            'source':      body.source,
            'target':      body.target,
            'timestamp':   body.timestamp or now,
            'received_at': now,
        }

        _pending_encrypted_packets.append(packet)

        save_log(
            'command_encrypted',
            source=body.source,
            data=f"sig={body.signature[:16]}...",
            encrypted=True,
        )

        return {
            'status':    'encrypted_command_received',
            'encrypted': True,
        }
    except Exception as e:
        logger.error(f"/command_encrypted error: {e}", exc_info=True)
        raise


# ── Эндпоинты для врача ───────────────────────────────────────────────────────

@app.get('/encrypted_packets')
def get_encrypted_packets(limit: int = Query(10, ge=1, le=100)):
    return {
        'packets':   _pending_encrypted_packets[-limit:],
        'total':     len(_pending_encrypted_packets),
        'encrypted': True,
        'note':      'Send packets to decryption_module/decrypt_packet',
    }


@app.get('/latest_encrypted_packet')
def get_latest_encrypted_packet():
    if not _pending_encrypted_packets:
        raise HTTPException(
            status_code=404, detail='No encrypted packets'
        )
    return {
        'packet':    _pending_encrypted_packets[-1],
        'encrypted': True,
        'note':      'Send to decryption_module/decrypt_packet',
    }


# ── Незашифрованные эндпоинты (legacy / fallback) ────────────────────────────

@app.post('/alarm')
async def receive_alarm(body: AlarmRequest):
    logger.warning(f"Unencrypted alarm received: {body.alarms}")
    save_log('alarm_unencrypted', data=str(body.alarms), encrypted=False)

    now = _now_iso()
    sent_count = 0

    if active_connections:
        for conn_key, websocket in list(active_connections.items()):
            try:
                await websocket.send_json({
                    'type':      'alarm',
                    'alarms':    body.alarms,
                    'encrypted': False,
                    'timestamp': now,
                })
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send alarm to {conn_key}: {e}")

    return {
        'status':    'alarm_sent',
        'sent_to':   sent_count,
        'alarms':    body.alarms,
        'encrypted': False,
    }


@app.post('/command')
async def command_from_doctor(body: CommandRequest):
    logger.info(f"Command from {body.source}: type={body.type}")
    save_log(
        'command', source=body.source,
        data=str(body.type), encrypted=False,
    )

    try:
        async with get_async_client() as c:
            if body.type == 'emergency_stop':
                await c.post(f'{MONITORING_URL}/emergency_stop')
                return {
                    'status': 'command_processed',
                    'action': 'emergency_stop',
                }
            elif body.type == 'set_max_torque':
                await c.post(
                    f'{SENSORS_URL}/set_max_torque',
                    json={'max_torque': body.data.get('max_torque', 50)},
                )
                return {
                    'status': 'command_processed',
                    'action': 'set_max_torque',
                }
            else:
                return {
                    'status': 'unknown_command',
                    'type':   body.type,
                }
    except Exception as e:
        logger.error(f"Command error: {e}")
        return {'status': 'error', 'error': str(e)}


# ── История ───────────────────────────────────────────────────────────────────

@app.get('/comms_history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(CommsLogDB)
            .order_by(CommsLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id':              l.id,
            'event_type':      l.event_type,
            'source':          l.source,
            'data':            l.data,
            'encrypted':       l.encrypted,
            'sent_to_doctors': l.sent_to_doctors,
            'created_at':      l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                               if l.created_at else None,
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)