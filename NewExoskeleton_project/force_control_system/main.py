# force_control_system.py
import os
import logging
from datetime import datetime
from enum import Enum

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = 8006
MODULE_NAME = os.getenv('MODULE_NAME', 'force_control_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///force_control.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class ForceStatus(str, Enum):
    IDLE = "idle"
    APPLYING = "applying"
    HOLDING = "holding"
    RELEASING = "releasing"
    OVERLOAD = "overload"
    EMERGENCY_STOP = "emergency_stop"


class ForceReadingDB(Base):
    __tablename__ = 'force_readings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    arm = Column(String(10))
    force_value = Column(Float)
    action = Column(String(50))
    timestamp = Column(DateTime, default=datetime.utcnow)


class ForceLogDB(Base):
    __tablename__ = 'force_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    arm = Column(String(10))
    action = Column(String(50))
    target_force = Column(Float)
    applied_force = Column(Float)
    status_before = Column(String(30))
    status_after = Column(String(30))
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

SAFETY_THRESHOLDS = {
    'max_safe_force': 150.0,
    'emergency_threshold': 200.0
}

force_state = {
    'left': {
        'status': ForceStatus.IDLE,
        'current_force': 0.0,
        'emergency_stop': False
    },
    'right': {
        'status': ForceStatus.IDLE,
        'current_force': 0.0,
        'emergency_stop': False
    }
}


class ApplyForceRequest(BaseModel):
    arm: str
    grip_type: str = 'power'
    target_force: float = 50
    max_force: float = 150


class ReleaseRequest(BaseModel):
    arm: str = None


app = FastAPI(title="Force Control System", version="2.0")


@app.post('/apply_force')
def apply_force(body: ApplyForceRequest):
    if body.arm not in force_state:
        raise HTTPException(status_code=400, detail='Invalid arm specified')
    if force_state[body.arm]['emergency_stop']:
        raise HTTPException(
            status_code=403, detail='Emergency stop is active'
        )

    state = force_state[body.arm]
    status_before = state['status'].value
    state['status'] = ForceStatus.APPLYING

    safe_force = min(
        body.target_force,
        body.max_force,
        SAFETY_THRESHOLDS['max_safe_force']
    )

    object_detected = safe_force > 5
    applied_force = safe_force

    state['current_force'] = applied_force
    state['status'] = (
        ForceStatus.HOLDING if object_detected else ForceStatus.IDLE
    )

    if applied_force > SAFETY_THRESHOLDS['emergency_threshold']:
        state['status'] = ForceStatus.OVERLOAD
        applied_force = SAFETY_THRESHOLDS['max_safe_force']
        state['current_force'] = applied_force
        logger.warning(
            f"OVERLOAD on {body.arm}: target={body.target_force}, "
            f"clamped to {applied_force}"
        )

    logger.info(
        f"Force applied: arm={body.arm}, target={body.target_force}, "
        f"actual={applied_force}, object={object_detected}"
    )

    session = SessionLocal()
    try:
        reading = ForceReadingDB(
            arm=body.arm,
            force_value=applied_force,
            action='apply'
        )
        session.add(reading)

        log = ForceLogDB(
            arm=body.arm,
            action='apply_force',
            target_force=body.target_force,
            applied_force=applied_force,
            status_before=status_before,
            status_after=state['status'].value
        )
        session.add(log)
        session.commit()
    finally:
        session.close()

    return {
        'success': True,
        'arm': body.arm,
        'target_force': body.target_force,
        'applied_force': round(applied_force, 2),
        'object_detected': object_detected,
        'status': state['status'].value
    }


@app.post('/release')
def release(body: ReleaseRequest = ReleaseRequest()):
    if body.arm and body.arm not in force_state:
        raise HTTPException(status_code=400, detail='Invalid arm')

    arms_to_release = [body.arm] if body.arm else ['left', 'right']

    session = SessionLocal()
    try:
        for current_arm in arms_to_release:
            state = force_state[current_arm]
            status_before = state['status'].value
            state['status'] = ForceStatus.RELEASING
            state['current_force'] = 0.0
            state['status'] = ForceStatus.IDLE

            logger.info(f"Force released: arm={current_arm}")

            log = ForceLogDB(
                arm=current_arm,
                action='release',
                target_force=0,
                applied_force=0,
                status_before=status_before,
                status_after=ForceStatus.IDLE.value
            )
            session.add(log)

        session.commit()
    finally:
        session.close()

    return {
        'success': True,
        'arms_released': arms_to_release,
        'status': ForceStatus.IDLE.value
    }


@app.get('/readings/{arm}')
def get_readings(arm: str):
    if arm not in force_state:
        raise HTTPException(status_code=404, detail='Invalid arm')
    state = force_state[arm]
    return {
        'arm': arm,
        'status': state['status'].value,
        'current_force': round(state['current_force'], 2)
    }


@app.get('/thresholds')
def get_thresholds():
    return SAFETY_THRESHOLDS


@app.get('/status')
def get_status():
    return {
        'left': {
            'status': force_state['left']['status'].value,
            'current_force': round(force_state['left']['current_force'], 2),
            'emergency_stop': force_state['left']['emergency_stop']
        },
        'right': {
            'status': force_state['right']['status'].value,
            'current_force': round(force_state['right']['current_force'], 2),
            'emergency_stop': force_state['right']['emergency_stop']
        },
        'thresholds': SAFETY_THRESHOLDS
    }


@app.get('/force_history')
def get_force_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(ForceLogDB)
            .order_by(ForceLogDB.created_at.desc())
            .limit(limit)
            .all()
        )
        return [{
            'id': l.id, 'arm': l.arm, 'action': l.action,
            'target_force': l.target_force,
            'applied_force': l.applied_force,
            'status_before': l.status_before,
            'status_after': l.status_after,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


@app.post('/emergency_stop')
def emergency_stop():
    for arm in force_state:
        force_state[arm]['emergency_stop'] = True
        force_state[arm]['status'] = ForceStatus.EMERGENCY_STOP
        force_state[arm]['current_force'] = 0.0
    logger.warning("EMERGENCY STOP activated")
    return {'message': 'Force control emergency stop activated'}


@app.post('/reset')
def reset():
    for arm in force_state:
        force_state[arm]['emergency_stop'] = False
        force_state[arm]['status'] = ForceStatus.IDLE
        force_state[arm]['current_force'] = 0.0
    logger.info("System reset")
    return {'message': 'Force control system reset'}


@app.get('/health')
def health_check():
    return {'status': 'healthy', 'module': MODULE_NAME}


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)