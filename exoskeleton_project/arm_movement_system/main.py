# arm_movement_system.py
import os
import logging
from datetime import datetime
from enum import Enum
from typing import Optional, List

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = 8002
MODULE_NAME = os.getenv('MODULE_NAME', 'arm_movement_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

UPPER_ARM_URL = 'http://localhost:8003'
MIDDLE_ARM_URL = 'http://localhost:8004'
FINGERS_URL = 'http://localhost:8005'
REQUEST_TIMEOUT = 5.0

INTENT_MAPPING = {
    'lift_arm': ['upper'],
    'lower_arm': ['upper'],
    'extend_arm': ['upper', 'middle'],
    'retract_arm': ['upper', 'middle'],
    'flex_elbow': ['middle'],
    'extend_elbow': ['middle'],
    'grasp': ['fingers'],
    'release': ['fingers']
}

# --- Database ---
DATABASE_URL = 'sqlite:///arm_movement.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class MovementHistoryDB(Base):
    __tablename__ = 'movement_history'

    id = Column(Integer, primary_key=True, autoincrement=True)
    arm = Column(String(20))
    intent = Column(String(50))
    strength = Column(Float)
    speed_modifier = Column(Float)
    sections_targeted = Column(String(100))
    success = Column(String(10), default='true')
    error_message = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


class ArmStatus(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    HOLDING = "holding"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


arm_state = {
    'left': {'status': ArmStatus.IDLE, 'position': {}},
    'right': {'status': ArmStatus.IDLE, 'position': {}},
    'emergency_stop': False
}


class ExecuteRequest(BaseModel):
    arm: str
    intent: str
    strength: float = 0.5
    speed_modifier: float = 1.0


app = FastAPI(title="Arm Movement System", version="2.0")


def get_section_url(section: str) -> Optional[str]:
    urls = {
        'upper': UPPER_ARM_URL,
        'middle': MIDDLE_ARM_URL,
        'fingers': FINGERS_URL
    }
    return urls.get(section)


@app.post('/execute', status_code=204)
def execute_movement(body: ExecuteRequest):
    if body.intent not in INTENT_MAPPING:
        raise HTTPException(
            status_code=400,
            detail=f'Unknown intent: {body.intent}'
        )

    if arm_state['emergency_stop']:
        logger.warning("Execute blocked: emergency stop is active")
        raise HTTPException(
            status_code=403,
            detail='Emergency stop is active'
        )

    sections = INTENT_MAPPING[body.intent]
    arms_to_move = (
        ['left', 'right'] if body.arm == 'both' else [body.arm]
    )

    logger.info(
        f"Executing movement: arm={body.arm}, intent={body.intent}, "
        f"strength={body.strength}, speed={body.speed_modifier}, "
        f"sections={sections}"
    )

    session = SessionLocal()
    errors = []

    for current_arm in arms_to_move:
        if current_arm not in arm_state:
            continue

        arm_state[current_arm]['status'] = ArmStatus.MOVING

        for section in sections:
            url = get_section_url(section)
            command = {
                'arm': current_arm,
                'intent': body.intent,
                'strength': body.strength,
                'speed_modifier': body.speed_modifier
            }

            try:
                with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                    resp = client.post(f'{url}/move', json=command)
                    logger.info(
                        f"Section {section} responded: {resp.status_code}"
                    )
            except Exception as e:
                error_msg = f"Section {section} error: {str(e)}"
                logger.error(error_msg)
                errors.append(error_msg)

        arm_state[current_arm]['status'] = ArmStatus.IDLE

    # Записываем историю
    try:
        history = MovementHistoryDB(
            arm=body.arm,
            intent=body.intent,
            strength=body.strength,
            speed_modifier=body.speed_modifier,
            sections_targeted=','.join(sections),
            success='true' if not errors else 'partial',
            error_message='; '.join(errors) if errors else None
        )
        session.add(history)
        session.commit()
    finally:
        session.close()

    return None


@app.post('/emergency_stop')
def emergency_stop():
    arm_state['emergency_stop'] = True
    arm_state['left']['status'] = ArmStatus.EMERGENCY_STOP
    arm_state['right']['status'] = ArmStatus.EMERGENCY_STOP
    logger.warning("EMERGENCY STOP activated")

    for url in [UPPER_ARM_URL, MIDDLE_ARM_URL, FINGERS_URL]:
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                client.post(f'{url}/emergency_stop')
        except Exception as e:
            logger.error(f"Emergency stop propagation failed: {e}")

    return {'message': 'Emergency stop activated'}


@app.post('/reset')
def reset():
    arm_state['emergency_stop'] = False
    arm_state['left']['status'] = ArmStatus.IDLE
    arm_state['right']['status'] = ArmStatus.IDLE
    logger.info("System reset")

    for url in [UPPER_ARM_URL, MIDDLE_ARM_URL, FINGERS_URL]:
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                client.post(f'{url}/reset')
        except Exception as e:
            logger.error(f"Reset propagation failed: {e}")

    return {'message': 'System reset complete'}


@app.get('/status')
def get_status():
    return {
        'left': {
            'status': arm_state['left']['status'].value,
            'position': arm_state['left']['position']
        },
        'right': {
            'status': arm_state['right']['status'].value,
            'position': arm_state['right']['position']
        },
        'emergency_stop': arm_state['emergency_stop']
    }


@app.get('/movement_history')
def get_movement_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(MovementHistoryDB)
            .order_by(MovementHistoryDB.created_at.desc())
            .limit(limit)
            .all()
        )
        return [{
            'id': l.id,
            'arm': l.arm,
            'intent': l.intent,
            'strength': l.strength,
            'speed_modifier': l.speed_modifier,
            'sections_targeted': l.sections_targeted,
            'success': l.success,
            'error_message': l.error_message,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


@app.get('/health')
def health_check():
    return {'status': 'healthy', 'module': MODULE_NAME}


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)