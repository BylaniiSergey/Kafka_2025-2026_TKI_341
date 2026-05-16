import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import threading
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker

from kafka_bus import EventBus, TOPIC_EMERGENCY

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5001))
MODULE_NAME = os.getenv('MODULE_NAME', 'emergency_control_module')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(MODULE_NAME)

EMERGENCY_OPEN_URL = os.getenv('EMERGENCY_OPEN_URL', 'http://localhost:5002')
EMERGENCY_STOP_URL = os.getenv('EMERGENCY_STOP_URL', 'http://localhost:5003')
REQUEST_TIMEOUT = 5.0

DATABASE_URL = 'sqlite:///emergency_control.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class EmergencyEventDB(Base):
    __tablename__ = 'emergency_events'
    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(100))
    reason = Column(String(200))
    transport = Column(String(20))
    open_result = Column(Boolean, nullable=True)
    stop_result = Column(Boolean, nullable=True)
    open_error = Column(Text, nullable=True)
    stop_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

state = {'emergency_active': False, 'last_source': None, 'last_reason': None,
         'total_events': 0, 'kafka_events': 0, 'http_events': 0}
_dispatch_lock = threading.Lock()


class EmergencySignalRequest(BaseModel):
    source: str
    reason: str = 'unspecified'


app = FastAPI(title="Emergency Control Module", version="1.1")
bus = EventBus(client_id=MODULE_NAME)


def _call_open(reason: str):
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(f'{EMERGENCY_OPEN_URL}/open', json={'source': MODULE_NAME, 'reason': reason})
            return resp.status_code == 200, None
    except Exception as e:
        return False, str(e)


def _call_stop(reason: str):
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(f'{EMERGENCY_STOP_URL}/safe_pose', json={'source': MODULE_NAME, 'reason': reason})
            return resp.status_code == 200, None
    except Exception as e:
        return False, str(e)


def _handle_emergency(source: str, reason: str, transport: str) -> dict:
    with _dispatch_lock:
        logger.critical(f"EMERGENCY [{transport}] from '{source}': {reason}")
        state['emergency_active'] = True
        state['last_source'] = source
        state['last_reason'] = reason
        state['total_events'] += 1
        if transport == 'kafka':
            state['kafka_events'] += 1
        else:
            state['http_events'] += 1
        open_ok, open_err = _call_open(reason)
        stop_ok, stop_err = _call_stop(reason)
        session = SessionLocal()
        try:
            session.add(EmergencyEventDB(source=source, reason=reason, transport=transport,
                                          open_result=open_ok, stop_result=stop_ok,
                                          open_error=open_err, stop_error=stop_err))
            session.commit()
        finally:
            session.close()
        return {'ok': True, 'transport': transport, 'source': source, 'reason': reason,
                'open_cabin': {'success': open_ok, 'error': open_err},
                'safe_pose': {'success': stop_ok, 'error': stop_err}}


def _on_kafka_message(payload: dict):
    source = str(payload.get('source', 'unknown'))
    reason = str(payload.get('reason', 'unspecified'))
    _handle_emergency(source, reason, transport='kafka')


@app.on_event('startup')
def on_startup():
    bus.subscribe(TOPIC_EMERGENCY, handler=_on_kafka_message, group_id='emergency-control')


@app.on_event('shutdown')
def on_shutdown():
    bus.close()


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def get_status():
    return {'service': MODULE_NAME, **state}


@app.post('/emergency')
def receive_emergency(body: EmergencySignalRequest):
    return _handle_emergency(body.source, body.reason, transport='http')


@app.post('/reset')
def reset_emergency(source: str = 'operator'):
    if source not in {'operator', 'doctor_tablet', 'rehab_center'}:
        raise HTTPException(status_code=403, detail=f'Source {source} not authorized to reset')
    state['emergency_active'] = False
    return {'ok': True, 'emergency_active': False, 'reset_by': source}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        events = session.query(EmergencyEventDB).order_by(EmergencyEventDB.created_at.desc()).limit(limit).all()
        return [{'id': e.id, 'source': e.source, 'reason': e.reason, 'transport': e.transport,
                 'open_result': e.open_result, 'stop_result': e.stop_result,
                 'created_at': e.created_at.strftime('%Y-%m-%d %H:%M:%S') if e.created_at else None} for e in events]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)