# emergency_control_module/main.py
import os
import logging
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String,
    Boolean, DateTime, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5201))
MODULE_NAME = os.getenv('MODULE_NAME', 'emergency_control_module')

EMERGENCY_OPEN_URL = os.getenv(
    'EMERGENCY_OPEN_URL', 'http://localhost:5202'
)
EMERGENCY_STOP_URL = os.getenv(
    'EMERGENCY_STOP_URL', 'http://localhost:5203'
)
REQUEST_TIMEOUT = 5.0

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///emergency_control.db'
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class EmergencyEventDB(Base):
    __tablename__ = 'emergency_events'
    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(100))
    reason = Column(String(200))
    open_result = Column(Boolean, nullable=True)
    stop_result = Column(Boolean, nullable=True)
    open_error = Column(Text, nullable=True)
    stop_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

state = {
    'emergency_active': False,
    'last_source': None,
    'last_reason': None,
    'total_events': 0
}

AUTHORIZED_SOURCES = {
    'critical_battery_monitor',
    'critical_situation_recognition',
    'temperature_measurement_system',
    'arm_force_limits_system',
    'leg_force_limits_system',
    'position_check_module',
    'task_orchestrator',
    'control_system',
    'monitoring_system',
    'doctor_tablet',
    'operator',
    'patient'
}


class EmergencySignalRequest(BaseModel):
    source: str
    reason: str = 'unspecified'


app = FastAPI(title="Emergency Control Module", version="1.1")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def get_status():
    return {
        'service': MODULE_NAME,
        'emergency_active': state['emergency_active'],
        'last_source': state['last_source'],
        'last_reason': state['last_reason'],
        'total_events': state['total_events']
    }


@app.post('/emergency')
def receive_emergency(body: EmergencySignalRequest):
    if body.source not in AUTHORIZED_SOURCES:
        raise HTTPException(
            status_code=403,
            detail=f'Source {body.source} not authorized'
        )

    logger.critical(
        f"EMERGENCY from '{body.source}': {body.reason}"
    )

    state['emergency_active'] = True
    state['last_source'] = body.source
    state['last_reason'] = body.reason
    state['total_events'] += 1

    open_result = None
    stop_result = None
    open_error = None
    stop_error = None

    # 1. Аварийное открытие кабины
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(
                f'{EMERGENCY_OPEN_URL}/open',
                json={
                    'source': MODULE_NAME,
                    'reason': body.reason
                }
            )
            open_result = resp.status_code == 200
            logger.info(
                f"Emergency open: HTTP {resp.status_code}"
            )
    except Exception as e:
        open_error = str(e)
        open_result = False
        logger.error(f"Emergency open failed: {e}")

    # 2. Безопасная поза
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(
                f'{EMERGENCY_STOP_URL}/safe_pose',
                json={
                    'source': MODULE_NAME,
                    'reason': body.reason
                }
            )
            stop_result = resp.status_code == 200
            logger.info(
                f"Safe pose: HTTP {resp.status_code}"
            )
    except Exception as e:
        stop_error = str(e)
        stop_result = False
        logger.error(f"Safe pose failed: {e}")

    session = SessionLocal()
    try:
        session.add(EmergencyEventDB(
            source=body.source,
            reason=body.reason,
            open_result=open_result,
            stop_result=stop_result,
            open_error=open_error,
            stop_error=stop_error
        ))
        session.commit()
    finally:
        session.close()

    return {
        'ok': True,
        'emergency_active': state['emergency_active'],
        'source': body.source,
        'reason': body.reason,
        'open_cabin': {
            'success': open_result,
            'error': open_error
        },
        'safe_pose': {
            'success': stop_result,
            'error': stop_error
        }
    }


@app.post('/reset')
def reset_emergency(source: str = 'operator'):
    authorized = {'operator', 'doctor_tablet', 'rehab_center'}
    if source not in authorized:
        raise HTTPException(
            status_code=403,
            detail=f'Source {source} not authorized to reset'
        )
    state['emergency_active'] = False
    logger.info(f"Emergency reset by {source}")
    return {
        'ok': True,
        'emergency_active': False,
        'reset_by': source
    }


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        events = (
            session.query(EmergencyEventDB)
            .order_by(EmergencyEventDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': e.id,
            'source': e.source,
            'reason': e.reason,
            'open_result': e.open_result,
            'stop_result': e.stop_result,
            'open_error': e.open_error,
            'stop_error': e.stop_error,
            'created_at': e.created_at.strftime(
                '%Y-%m-%d %H:%M:%S'
            ) if e.created_at else None
        } for e in events]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)