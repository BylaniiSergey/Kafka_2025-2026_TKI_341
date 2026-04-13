# fingers_system.py
import os
import logging
from datetime import datetime
from enum import Enum

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, Boolean, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = 8005
MODULE_NAME = os.getenv('MODULE_NAME', 'fingers_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

FORCE_CONTROL_URL = 'http://localhost:8006'
REQUEST_TIMEOUT = 5.0

DATABASE_URL = 'sqlite:///fingers.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class FingerStatus(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    GRASPING = "grasping"
    HOLDING = "holding"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


class GripState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    PARTIAL = "partial"


class GripExecutionDB(Base):
    __tablename__ = 'grip_executions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    arm = Column(String(10))
    intent = Column(String(50))
    grip_percentage = Column(Float)
    target_force = Column(Float)
    actual_force = Column(Float)
    object_detected = Column(Boolean)
    success = Column(Boolean)
    executed_at = Column(DateTime, default=datetime.utcnow)


class MovementLogDB(Base):
    __tablename__ = 'movement_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    arm = Column(String(10))
    intent = Column(String(50))
    grip_before = Column(Float)
    grip_after = Column(Float)
    force_before = Column(Float)
    force_after = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

finger_states = {
    'left': {
        'status': FingerStatus.IDLE,
        'grip_percentage': 0.0,
        'grip_state': GripState.OPEN,
        'grip_force': 0.0,
        'emergency_stop': False
    },
    'right': {
        'status': FingerStatus.IDLE,
        'grip_percentage': 0.0,
        'grip_state': GripState.OPEN,
        'grip_force': 0.0,
        'emergency_stop': False
    }
}


class MoveRequest(BaseModel):
    arm: str
    intent: str
    strength: float = 0.5
    speed_modifier: float = 1.0


app = FastAPI(title="Fingers System", version="2.0")


def execute_grasp(arm: str, strength: float) -> dict:
    state = finger_states[arm]
    grip_before = state['grip_percentage']
    force_before = state['grip_force']

    state['status'] = FingerStatus.GRASPING
    grip_percentage = min(100.0, strength * 100)

    force_command = {
        'arm': arm,
        'grip_type': 'power',
        'target_force': strength * 100,
        'max_force': 150
    }

    actual_force = 0.0
    object_detected = False

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.post(
                f'{FORCE_CONTROL_URL}/apply_force',
                json=force_command
            )
            if resp.status_code == 200:
                force_data = resp.json()
                actual_force = force_data.get('applied_force', 0)
                object_detected = force_data.get('object_detected', False)
                logger.info(
                    f"Force control response: force={actual_force}, "
                    f"object={object_detected}"
                )
    except Exception as e:
        logger.warning(f"Force control unavailable: {e}")
        actual_force = strength * 50
        object_detected = True

    state['grip_percentage'] = grip_percentage
    state['grip_force'] = actual_force
    state['grip_state'] = (
        GripState.CLOSED if grip_percentage > 80 else GripState.PARTIAL
    )
    state['status'] = (
        FingerStatus.HOLDING if object_detected else FingerStatus.IDLE
    )

    logger.info(
        f"Grasp executed: arm={arm}, grip={grip_percentage}%, "
        f"force={actual_force}, object={object_detected}"
    )

    session = SessionLocal()
    try:
        grip_record = GripExecutionDB(
            arm=arm, intent='grasp', grip_percentage=grip_percentage,
            target_force=strength * 100, actual_force=actual_force,
            object_detected=object_detected, success=object_detected
        )
        session.add(grip_record)

        log_entry = MovementLogDB(
            arm=arm, intent='grasp',
            grip_before=grip_before, grip_after=grip_percentage,
            force_before=force_before, force_after=actual_force
        )
        session.add(log_entry)
        session.commit()
    finally:
        session.close()

    return {
        'success': True,
        'arm': arm,
        'grip_percentage': grip_percentage,
        'grip_force': actual_force,
        'object_detected': object_detected,
        'grip_state': state['grip_state'].value
    }


def execute_release(arm: str) -> dict:
    state = finger_states[arm]
    grip_before = state['grip_percentage']
    force_before = state['grip_force']

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            client.post(
                f'{FORCE_CONTROL_URL}/release', json={'arm': arm}
            )
    except Exception as e:
        logger.warning(f"Force control release failed: {e}")

    state['grip_percentage'] = 0.0
    state['grip_force'] = 0.0
    state['grip_state'] = GripState.OPEN
    state['status'] = FingerStatus.IDLE

    logger.info(f"Release executed: arm={arm}")

    session = SessionLocal()
    try:
        grip_record = GripExecutionDB(
            arm=arm, intent='release', grip_percentage=0,
            target_force=0, actual_force=0,
            object_detected=False, success=True
        )
        session.add(grip_record)

        log_entry = MovementLogDB(
            arm=arm, intent='release',
            grip_before=grip_before, grip_after=0.0,
            force_before=force_before, force_after=0.0
        )
        session.add(log_entry)
        session.commit()
    finally:
        session.close()

    return {
        'success': True,
        'arm': arm,
        'grip_percentage': 0,
        'grip_force': 0,
        'grip_state': GripState.OPEN.value
    }


@app.post('/move')
def move(body: MoveRequest):
    if body.arm not in finger_states:
        raise HTTPException(status_code=400, detail='Invalid arm specified')
    if finger_states[body.arm]['emergency_stop']:
        raise HTTPException(
            status_code=403, detail='Emergency stop is active'
        )

    if body.intent == 'grasp':
        return execute_grasp(body.arm, body.strength)
    elif body.intent == 'release':
        return execute_release(body.arm)

    raise HTTPException(
        status_code=400, detail=f'Unknown intent: {body.intent}'
    )


@app.get('/status')
def get_status():
    return {
        arm: {
            'status': finger_states[arm]['status'].value,
            'grip_percentage': finger_states[arm]['grip_percentage'],
            'grip_state': finger_states[arm]['grip_state'].value,
            'grip_force': finger_states[arm]['grip_force'],
            'emergency_stop': finger_states[arm]['emergency_stop']
        }
        for arm in ['left', 'right']
    }


@app.get('/positions/{arm}')
def get_positions(arm: str):
    if arm not in finger_states:
        raise HTTPException(status_code=404, detail='Invalid arm')
    state = finger_states[arm]
    return {
        'arm': arm,
        'grip_percentage': state['grip_percentage'],
        'grip_state': state['grip_state'].value,
        'grip_force': state['grip_force'],
        'status': state['status'].value
    }


@app.post('/emergency_stop')
def emergency_stop():
    for arm in finger_states:
        finger_states[arm]['emergency_stop'] = True
        finger_states[arm]['status'] = FingerStatus.EMERGENCY_STOP
    logger.warning("EMERGENCY STOP activated")

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            client.post(f'{FORCE_CONTROL_URL}/emergency_stop')
    except Exception as e:
        logger.error(f"Force control emergency stop failed: {e}")

    return {'message': 'Fingers emergency stop activated'}


@app.post('/reset')
def reset():
    for arm in finger_states:
        finger_states[arm]['emergency_stop'] = False
        finger_states[arm]['status'] = FingerStatus.IDLE
        finger_states[arm]['grip_percentage'] = 0.0
        finger_states[arm]['grip_state'] = GripState.OPEN
        finger_states[arm]['grip_force'] = 0.0

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            client.post(f'{FORCE_CONTROL_URL}/reset')
    except Exception as e:
        logger.error(f"Force control reset failed: {e}")

    logger.info("System reset")
    return {'message': 'Fingers system reset'}


@app.get('/history')
def get_history(limit: int = Query(50, ge=1, le=1000)):
    session = SessionLocal()
    try:
        records = (
            session.query(GripExecutionDB)
            .order_by(GripExecutionDB.executed_at.desc())
            .limit(limit)
            .all()
        )
        return [{
            'id': r.id, 'arm': r.arm, 'intent': r.intent,
            'grip_percentage': r.grip_percentage,
            'target_force': r.target_force,
            'actual_force': r.actual_force,
            'object_detected': r.object_detected,
            'success': r.success,
            'executed_at': r.executed_at.strftime('%Y-%m-%d %H:%M:%S')
                if r.executed_at else None
        } for r in records]
    finally:
        session.close()


@app.get('/movement_history')
def get_movement_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(MovementLogDB)
            .order_by(MovementLogDB.created_at.desc())
            .limit(limit)
            .all()
        )
        return [{
            'id': l.id, 'arm': l.arm, 'intent': l.intent,
            'grip_before': l.grip_before, 'grip_after': l.grip_after,
            'force_before': l.force_before, 'force_after': l.force_after,
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