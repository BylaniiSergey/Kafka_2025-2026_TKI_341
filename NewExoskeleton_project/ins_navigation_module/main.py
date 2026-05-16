# ins_navigation_module/main.py
import os
import logging
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, DateTime, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5007))
MODULE_NAME = os.getenv('MODULE_NAME', 'ins_navigation_module')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

POSITION_CHECK_URL = os.getenv(
    'POSITION_CHECK_URL', 'http://localhost:5005'
)
REQUEST_TIMEOUT = 5.0

# Допустимая зона: квадрат ±5 шагов от нулевой точки
ZONE_LIMIT = 5
STEP_SIZE = 1.0

DATABASE_URL = 'sqlite:///ins_navigation.db'
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class INSPositionDB(Base):
    __tablename__ = 'ins_positions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    x = Column(Float)
    y = Column(Float)
    intent = Column(String(50), nullable=True)
    in_zone = Column(Boolean)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

ins_state = {
    'x': 0.0,
    'y': 0.0,
    'in_zone': True,
    'step_count': {
        'move_forward': 0,
        'move_backward': 0,
        'turn_left': 0,
        'turn_right': 0,
    }
}

INTENT_TO_VECTOR = {
    'move_forward':  (0.0,  STEP_SIZE),
    'move_backward': (0.0, -STEP_SIZE),
    'turn_left':     (-STEP_SIZE, 0.0),
    'turn_right':    ( STEP_SIZE, 0.0),
    'pivot_left':    (-STEP_SIZE, 0.0),
    'pivot_right':   ( STEP_SIZE, 0.0),
}


class MovementEventRequest(BaseModel):
    intent: str
    steps: int = 1


class ManualPositionRequest(BaseModel):
    x: float
    y: float


def is_in_zone(x: float, y: float) -> bool:
    return abs(x) <= ZONE_LIMIT and abs(y) <= ZONE_LIMIT


def save_position(intent: str = None):
    session = SessionLocal()
    try:
        session.add(INSPositionDB(
            x=ins_state['x'],
            y=ins_state['y'],
            intent=intent,
            in_zone=ins_state['in_zone']
        ))
        session.commit()
    finally:
        session.close()


app = FastAPI(title="INS Navigation Module", version="1.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/position')
def get_position():
    return {
        'service': MODULE_NAME,
        'x': ins_state['x'],
        'y': ins_state['y'],
        'in_zone': ins_state['in_zone'],
        'zone_limit': ZONE_LIMIT,
        'step_count': ins_state['step_count']
    }


@app.post('/movement_event')
def receive_movement_event(body: MovementEventRequest):
    """
    Принимает событие движения от модулей ног.
    ИНС — эталонный источник позиции (всегда прав).
    """
    dx, dy = INTENT_TO_VECTOR.get(body.intent, (0.0, 0.0))
    if dx == 0.0 and dy == 0.0:
        return {
            'ok': True,
            'message': f'Intent {body.intent} does not affect position',
            'position': {'x': ins_state['x'], 'y': ins_state['y']}
        }

    for _ in range(body.steps):
        ins_state['x'] += dx
        ins_state['y'] += dy

    if body.intent in ins_state['step_count']:
        ins_state['step_count'][body.intent] += body.steps

    in_zone = is_in_zone(ins_state['x'], ins_state['y'])
    ins_state['in_zone'] = in_zone

    logger.info(
        f"INS position: x={ins_state['x']:.1f}, "
        f"y={ins_state['y']:.1f}, in_zone={in_zone}, "
        f"intent={body.intent}"
    )

    save_position(body.intent)

    # Уведомить position_check_module
    notify_result = None
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(
                f'{POSITION_CHECK_URL}/ins_update',
                json={
                    'x': ins_state['x'],
                    'y': ins_state['y'],
                    'in_zone': in_zone,
                    'intent': body.intent
                }
            )
            notify_result = resp.json()
    except Exception as e:
        logger.error(f"Position check notify failed: {e}")
        notify_result = {'error': str(e)}

    return {
        'ok': True,
        'position': {
            'x': ins_state['x'],
            'y': ins_state['y']
        },
        'in_zone': in_zone,
        'intent': body.intent,
        'steps': body.steps,
        'position_check_notified': notify_result
    }


@app.post('/set_position')
def set_position(body: ManualPositionRequest):
    ins_state['x'] = body.x
    ins_state['y'] = body.y
    ins_state['in_zone'] = is_in_zone(body.x, body.y)
    save_position('manual_set')
    return {
        'ok': True,
        'position': {'x': ins_state['x'], 'y': ins_state['y']},
        'in_zone': ins_state['in_zone']
    }


@app.post('/reset_position')
def reset_position():
    ins_state['x'] = 0.0
    ins_state['y'] = 0.0
    ins_state['in_zone'] = True
    ins_state['step_count'] = {k: 0 for k in ins_state['step_count']}
    save_position('reset')
    return {
        'ok': True,
        'position': {'x': 0.0, 'y': 0.0},
        'in_zone': True
    }


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        positions = (
            session.query(INSPositionDB)
            .order_by(INSPositionDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': p.id,
            'x': p.x,
            'y': p.y,
            'intent': p.intent,
            'in_zone': p.in_zone,
            'created_at': p.created_at.strftime('%Y-%m-%d %H:%M:%S')
            if p.created_at else None
        } for p in positions]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)