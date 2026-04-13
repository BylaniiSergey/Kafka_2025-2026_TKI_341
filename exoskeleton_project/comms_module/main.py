# comms_module/main.py
import os
import logging
import asyncio
from datetime import datetime
from typing import Dict

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 6001))
MODULE_NAME = os.getenv('MODULE_NAME', 'comms_module')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

MONITORING_URL = os.getenv('MONITORING_URL', 'http://localhost:6002')
SENSORS_URL = os.getenv('SENSORS_URL', 'http://localhost:6003')
REQUEST_TIMEOUT = 5.0

DATABASE_URL = 'sqlite:///comms_module.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class CommsLogDB(Base):
    __tablename__ = 'comms_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50))
    source = Column(String(50), nullable=True)
    data = Column(Text, nullable=True)
    sent_to_doctors = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

active_connections: Dict[str, WebSocket] = {}


def save_log(
    event_type: str, source: str = None,
    data: str = None, sent: bool = False
):
    session = SessionLocal()
    try:
        session.add(CommsLogDB(
            event_type=event_type,
            source=source,
            data=data,
            sent_to_doctors=sent
        ))
        session.commit()
    finally:
        session.close()


class AlarmRequest(BaseModel):
    alarms: list


class CommandRequest(BaseModel):
    type: str
    source: str = 'doctor'
    data: dict = {}


app = FastAPI(title="Comms Module", version="2.0")


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
    """Врач подключается для получения телеметрии в реальном времени"""
    await websocket.accept()
    conn_key = f"doctor_{doctor_id}"
    active_connections[conn_key] = websocket
    logger.info(f"Doctor {doctor_id} connected via WebSocket")
    save_log('ws_connect', source=doctor_id)

    try:
        while True:
            try:
                async with httpx.AsyncClient(
                    timeout=REQUEST_TIMEOUT
                ) as client:
                    telemetry = await client.get(
                        f'{MONITORING_URL}/telemetry'
                    )
                    await websocket.send_json(telemetry.json())
            except Exception as e:
                await websocket.send_json({'error': str(e)})

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        logger.info(f"Doctor {doctor_id} disconnected")
        save_log('ws_disconnect', source=doctor_id)
    finally:
        if conn_key in active_connections:
            del active_connections[conn_key]


@app.post('/alarm')
async def receive_alarm(body: AlarmRequest):
    """
    Получить аларм от мониторинга
    и разослать всем подключённым врачам
    """
    logger.warning(f"Alarm received: {body.alarms}")
    save_log('alarm', data=str(body.alarms))

    sent_count = 0
    for conn_key, websocket in list(active_connections.items()):
        try:
            await websocket.send_json({
                'type': 'alarm',
                'alarms': body.alarms,
                'timestamp': datetime.now().isoformat()
            })
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send alarm to {conn_key}: {e}")

    save_log('alarm_sent', data=str(body.alarms), sent=True)
    return {
        'status': 'alarm_sent',
        'sent_to': sent_count,
        'alarms': body.alarms
    }


@app.post('/command')
async def command_from_doctor(body: CommandRequest):
    """
    Принять команду от врача и отправить в систему
    """
    logger.info(
        f"Command from {body.source}: type={body.type}"
    )
    save_log('command', source=body.source, data=str(body.type))

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as c:
            if body.type == 'emergency_stop':
                await c.post(f'{MONITORING_URL}/emergency_stop')
                return {
                    'status': 'command_processed',
                    'action': 'emergency_stop'
                }

            elif body.type == 'set_max_torque':
                await c.post(
                    f'{SENSORS_URL}/set_max_torque',
                    json={'max_torque': body.data.get('max_torque', 50)}
                )
                return {
                    'status': 'command_processed',
                    'action': 'set_max_torque'
                }

            else:
                return {
                    'status': 'unknown_command',
                    'type': body.type
                }

    except Exception as e:
        logger.error(f"Command error: {e}")
        return {'status': 'error', 'error': str(e)}


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
            'id': l.id, 'event_type': l.event_type,
            'source': l.source, 'data': l.data,
            'sent_to_doctors': l.sent_to_doctors,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)