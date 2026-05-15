# leg_movement_system/main.py
import os
import logging
from datetime import datetime
from enum import Enum
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float,
    String, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 9002))
MODULE_NAME = os.getenv('MODULE_NAME', 'leg_movement_system')

KNEE_SYSTEM_URL = os.getenv(
    'KNEE_SYSTEM_URL', 'http://localhost:9003'
)
TRACK_SYSTEM_URL = os.getenv(
    'TRACK_SYSTEM_URL', 'http://localhost:9004'
)
LEG_FORCE_URL = os.getenv(
    'LEG_FORCE_URL', 'http://localhost:9006'
)
GNSS_URL = os.getenv(
    'GNSS_URL', 'http://localhost:5205'
)
INS_URL = os.getenv(
    'INS_URL', 'http://localhost:5206'
)
REQUEST_TIMEOUT = 5.0

INTENT_MAPPING = {
    'flex_knee': ['knee'],
    'extend_knee': ['knee'],
    'squat': ['knee'],
    'stand_up': ['knee'],
    'sit_down': ['knee'],
    'move_forward': ['track'],
    'move_backward': ['track'],
    'turn_left': ['track'],
    'turn_right': ['track'],
    'pivot_left': ['track'],
    'pivot_right': ['track'],
    'stop': ['track'],
    'brake': ['track', 'knee']
}

NAV_INTENTS = {
    'move_forward', 'move_backward',
    'turn_left', 'turn_right',
    'pivot_left', 'pivot_right'
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///leg_movement.db'
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class MovementHistoryDB(Base):
    __tablename__ = 'movement_history'
    id = Column(Integer, primary_key=True, autoincrement=True)
    leg = Column(String(20))
    intent = Column(String(50))
    strength = Column(Float)
    speed_modifier = Column(Float)
    systems_targeted = Column(String(100))
    success = Column(String(10), default='true')
    error_message = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


class SystemStatus(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    STANDING = "standing"
    DRIVING = "driving"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


system_state = {
    'status': SystemStatus.IDLE,
    'emergency_stop': False,
    'current_intent': None
}


class ExecuteRequest(BaseModel):
    leg: str = 'both'
    intent: str
    strength: float = 0.5
    speed_modifier: float = 1.0


app = FastAPI(title="Leg Movement System", version="2.1")


def get_system_url(system: str) -> Optional[str]:
    return {
        'knee': KNEE_SYSTEM_URL,
        'track': TRACK_SYSTEM_URL
    }.get(system)


def notify_navigation(intent: str):
    for nav_url in [GNSS_URL, INS_URL]:
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
                c.post(
                    f'{nav_url}/movement_event',
                    json={'intent': intent, 'steps': 1}
                )
        except Exception as e:
            logger.error(
                f"Navigation notify failed ({nav_url}): {e}"
            )


@app.post('/execute')
def execute_movement(body: ExecuteRequest):
    if body.intent not in INTENT_MAPPING:
        raise HTTPException(
            status_code=400,
            detail=f'Unknown intent: {body.intent}'
        )
    if system_state['emergency_stop']:
        raise HTTPException(
            status_code=403,
            detail='Emergency stop is active'
        )

    systems = INTENT_MAPPING[body.intent]
    system_state['status'] = SystemStatus.MOVING
    system_state['current_intent'] = body.intent

    logger.info(
        f"Leg movement: leg={body.leg}, "
        f"intent={body.intent}, systems={systems}"
    )

    errors = []
    results = {}

    for system in systems:
        url = get_system_url(system)
        command = {
            'leg': body.leg,
            'intent': body.intent,
            'strength': body.strength,
            'speed_modifier': body.speed_modifier
        }
        try:
            with httpx.Client(
                timeout=REQUEST_TIMEOUT
            ) as client:
                resp = client.post(
                    f'{url}/move', json=command
                )
                results[system] = (
                    resp.json()
                    if resp.status_code == 200
                    else {}
                )
                logger.info(
                    f"System {system}: {resp.status_code}"
                )
        except Exception as e:
            error_msg = f"System {system} error: {str(e)}"
            logger.error(error_msg)
            errors.append(error_msg)
            results[system] = {'error': str(e)}

    if body.intent in ['move_forward', 'move_backward',
                        'turn_left', 'turn_right']:
        system_state['status'] = SystemStatus.DRIVING
    elif body.intent == 'stand_up':
        system_state['status'] = SystemStatus.STANDING
    else:
        system_state['status'] = SystemStatus.IDLE

    # Уведомление навигационных модулей
    if body.intent in NAV_INTENTS and not errors:
        notify_navigation(body.intent)

    session = SessionLocal()
    try:
        session.add(MovementHistoryDB(
            leg=body.leg,
            intent=body.intent,
            strength=body.strength,
            speed_modifier=body.speed_modifier,
            systems_targeted=','.join(systems),
            success='true' if not errors else 'partial',
            error_message=(
                '; '.join(errors) if errors else None
            )
        ))
        session.commit()
    finally:
        session.close()

    return {
        'success': True,
        'intent': body.intent,
        'systems_called': systems,
        'results': results
    }


@app.post('/emergency_stop')
def emergency_stop():
    system_state['emergency_stop'] = True
    system_state['status'] = SystemStatus.EMERGENCY_STOP
    logger.warning("EMERGENCY STOP activated")

    for url in [KNEE_SYSTEM_URL, TRACK_SYSTEM_URL, LEG_FORCE_URL]:
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
                c.post(f'{url}/emergency_stop')
        except Exception as e:
            logger.error(f"Emergency stop failed: {e}")

    return {'message': 'Emergency stop activated'}


@app.post('/reset')
def reset():
    system_state['emergency_stop'] = False
    system_state['status'] = SystemStatus.IDLE
    system_state['current_intent'] = None
    logger.info("System reset")

    for url in [KNEE_SYSTEM_URL, TRACK_SYSTEM_URL, LEG_FORCE_URL]:
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
                c.post(f'{url}/reset')
        except Exception as e:
            logger.error(f"Reset failed: {e}")

    return {'message': 'All systems reset'}


@app.get('/status')
def get_status():
    return {
        'main_status': system_state['status'].value,
        'emergency_stop': system_state['emergency_stop'],
        'current_intent': system_state['current_intent']
    }


@app.get('/movement_history')
def get_movement_history(
    limit: int = Query(100, ge=1, le=1000)
):
    session = SessionLocal()
    try:
        logs = (
            session.query(MovementHistoryDB)
            .order_by(MovementHistoryDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id,
            'leg': l.leg,
            'intent': l.intent,
            'strength': l.strength,
            'speed_modifier': l.speed_modifier,
            'systems_targeted': l.systems_targeted,
            'success': l.success,
            'error_message': l.error_message,
            'created_at': l.created_at.strftime(
                '%Y-%m-%d %H:%M:%S'
            ) if l.created_at else None
        } for l in logs]
    finally:
        session.close()


@app.get('/health')
def health_check():
    return {'status': 'healthy', 'module': MODULE_NAME}


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)