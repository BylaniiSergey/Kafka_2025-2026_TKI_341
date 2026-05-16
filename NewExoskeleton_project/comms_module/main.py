# comms_module/main.py
import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Dict

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String,
    Boolean, DateTime, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 6001))
MODULE_NAME = os.getenv('MODULE_NAME', 'comms_module')

MONITORING_URL = os.getenv(
    'MONITORING_URL', 'http://localhost:6002'
)
CRYPTO_URL = os.getenv(
    'CRYPTO_URL', 'http://localhost:5102'
)
TASK_ORCHESTRATOR_URL = os.getenv(
    'TASK_ORCHESTRATOR_URL', 'http://localhost:5000'
)
REQUEST_TIMEOUT = 5.0

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///comms_module.db'
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class CommsLogDB(Base):
    __tablename__ = 'comms_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50))
    source = Column(String(50), nullable=True)
    data = Column(Text, nullable=True)
    sent_to_doctors = Column(Boolean, default=False)
    encrypted = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

active_connections: Dict[str, WebSocket] = {}


def save_log(
    event_type: str,
    source: str = None,
    data: str = None,
    sent: bool = False,
    encrypted: bool = False
):
    session = SessionLocal()
    try:
        session.add(CommsLogDB(
            event_type=event_type,
            source=source,
            data=data,
            sent_to_doctors=sent,
            encrypted=encrypted
        ))
        session.commit()
    finally:
        session.close()


async def encrypt_payload(payload: dict, target: str) -> dict:
    """
    Шифрует данные перед отправкой врачу.
    Без успешного шифрования телеметрия не отправляется.
    """
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                f'{CRYPTO_URL}/encrypt',
                json={
                    'plaintext': json.dumps(payload, ensure_ascii=False),
                    'source': MODULE_NAME,
                    'target': target
                }
            )
            resp.raise_for_status()
            return {
                'ok': True,
                'encrypted': True,
                **resp.json()
            }
    except Exception as e:
        logger.error(f"Encryption failed for {target}: {e}")
        return {
            'ok': False,
            'encrypted': False,
            'error': str(e)
        }


class AlarmRequest(BaseModel):
    alarms: list


class CommandRequest(BaseModel):
    type: str
    source: str = 'doctor_tablet'
    data: dict = {}
    verification_token: str = ""


app = FastAPI(title="Comms Module", version="2.2")


@app.get('/health')
def health():
    return {
        'status': 'ok',
        'service': MODULE_NAME,
        'active_connections': len(active_connections)
    }


@app.get('/status')
def status():
    return {
        'service': MODULE_NAME,
        'active_connections': list(active_connections.keys()),
        'total_connections': len(active_connections)
    }


@app.websocket('/doctor/{doctor_id}')
async def doctor_endpoint(websocket: WebSocket, doctor_id: str):
    """
    Врач подключается для получения телеметрии.
    Телеметрия уходит только в зашифрованном виде.
    """
    await websocket.accept()
    conn_key = f'doctor_{doctor_id}'
    active_connections[conn_key] = websocket
    logger.info(f"Doctor {doctor_id} connected")
    save_log('ws_connect', source=doctor_id)

    try:
        while True:
            try:
                async with httpx.AsyncClient(
                    timeout=REQUEST_TIMEOUT
                ) as client:
                    telemetry_resp = await client.get(
                        f'{MONITORING_URL}/telemetry'
                    )
                    telemetry_resp.raise_for_status()
                    telemetry = telemetry_resp.json()

                encrypted = await encrypt_payload(
                    telemetry,
                    target=conn_key
                )

                if encrypted.get('ok'):
                    await websocket.send_json({
                        'type': 'encrypted_telemetry',
                        'ciphertext': encrypted['ciphertext'],
                        'signature': encrypted['signature'],
                        'timestamp': encrypted['timestamp']
                    })
                    save_log(
                        'telemetry_sent',
                        source=doctor_id,
                        sent=True,
                        encrypted=True
                    )
                else:
                    await websocket.send_json({
                        'type': 'error',
                        'error': 'encryption_failed',
                        'detail': encrypted.get('error')
                    })

            except Exception as e:
                await websocket.send_json({
                    'type': 'error',
                    'error': str(e)
                })

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        logger.info(f"Doctor {doctor_id} disconnected")
        save_log('ws_disconnect', source=doctor_id)
    finally:
        active_connections.pop(conn_key, None)


@app.post('/alarm')
async def receive_alarm(body: AlarmRequest):
    """
    Получить аларм от monitoring_system и разослать врачам.
    Алармы тоже шифруются.
    """
    logger.warning(f"Alarm received: {body.alarms}")
    save_log('alarm', data=str(body.alarms))

    sent_count = 0

    for conn_key, websocket in list(active_connections.items()):
        try:
            payload = {
                'type': 'alarm',
                'alarms': body.alarms,
                'timestamp': datetime.now().isoformat()
            }

            encrypted = await encrypt_payload(
                payload,
                target=conn_key
            )

            if not encrypted.get('ok'):
                logger.error(
                    f"Failed to encrypt alarm for {conn_key}: "
                    f"{encrypted.get('error')}"
                )
                continue

            await websocket.send_json({
                'type': 'encrypted_alarm',
                'ciphertext': encrypted['ciphertext'],
                'signature': encrypted['signature'],
                'timestamp': encrypted['timestamp']
            })
            sent_count += 1

        except Exception as e:
            logger.error(
                f"Failed to send alarm to {conn_key}: {e}"
            )

    save_log(
        'alarm_sent',
        data=str(body.alarms),
        sent=True,
        encrypted=True
    )

    return {
        'status': 'alarm_sent',
        'sent_to': sent_count,
        'alarms': body.alarms
    }


@app.post('/command')
async def command_from_doctor(body: CommandRequest):
    """
    Команда врача больше не маршрутизируется напрямую в monitoring/sensors.
    Она уходит в task_orchestrator, который уже сам:
      - вызывает command_verification
      - решает emergency/control маршрут
    """
    logger.info(
        f"Doctor command: source={body.source}, type={body.type}"
    )
    save_log(
        'command',
        source=body.source,
        data=str({
            'type': body.type,
            'data': body.data
        })
    )

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                f'{TASK_ORCHESTRATOR_URL}/dispatch',
                json={
                    'source': body.source,
                    'command': body.type,
                    'verification_token': body.verification_token,
                    'payload': body.data
                }
            )
            return {
                'status': 'forwarded_to_orchestrator',
                'orchestrator_status': resp.status_code,
                'result': resp.json()
            }
    except Exception as e:
        logger.error(f"Command forwarding error: {e}")
        return {
            'status': 'error',
            'error': str(e)
        }


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
            'id': l.id,
            'event_type': l.event_type,
            'source': l.source,
            'data': l.data,
            'sent_to_doctors': l.sent_to_doctors,
            'encrypted': l.encrypted,
            'created_at': l.created_at.strftime(
                '%Y-%m-%d %H:%M:%S'
            ) if l.created_at else None
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)