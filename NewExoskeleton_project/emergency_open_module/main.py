import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5002))
MODULE_NAME = os.getenv('MODULE_NAME', 'emergency_open_module')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///emergency_open.db'
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class OpenEventDB(Base):
    __tablename__ = 'open_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(100))
    reason = Column(Text)
    cabin_opened = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

cabin_state = {
    'is_open': False,
    'total_openings': 0,
    'last_open_reason': None,
}


class OpenRequest(BaseModel):
    source: str = 'emergency_control_module'
    reason: str = 'emergency'


app = FastAPI(title="Emergency Open Module", version="1.1")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def get_status():
    return {
        'service': MODULE_NAME,
        'cabin_is_open': cabin_state['is_open'],
        'total_openings': cabin_state['total_openings'],
        'last_open_reason': cabin_state['last_open_reason'],
    }


@app.post('/open')
def open_cabin(body: OpenRequest):
    logger.critical(
        f"EMERGENCY CABIN OPEN: source={body.source}, "
        f"reason={body.reason}"
    )

    cabin_state['is_open'] = True
    cabin_state['total_openings'] += 1
    cabin_state['last_open_reason'] = body.reason

    session = SessionLocal()
    try:
        session.add(OpenEventDB(
            source=body.source,
            reason=body.reason,
            cabin_opened=True,
        ))
        session.commit()
    finally:
        session.close()

    return {
        'ok': True,
        'cabin_opened': True,
        'source': body.source,
        'reason': body.reason,
    }


@app.post('/close')
def close_cabin(source: str = 'operator'):
    cabin_state['is_open'] = False
    return {'ok': True, 'cabin_is_open': False}


@app.post('/reset')
def reset():
    cabin_state['is_open'] = False
    cabin_state['total_openings'] = 0
    cabin_state['last_open_reason'] = None
    return {'ok': True}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        events = (
            session.query(OpenEventDB)
            .order_by(OpenEventDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': e.id, 'source': e.source, 'reason': e.reason,
            'cabin_opened': e.cabin_opened,
            'created_at': e.created_at.strftime('%Y-%m-%d %H:%M:%S')
            if e.created_at else None,
        } for e in events]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)