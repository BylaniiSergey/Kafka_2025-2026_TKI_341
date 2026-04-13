import os
import logging
from datetime import datetime
from enum import Enum

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, Boolean, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = 9003
MODULE_NAME = os.getenv('MODULE_NAME', 'knee_belt_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///knee_belt.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class KneeStatus(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    FLEXED = "flexed"
    EXTENDED = "extended"
    LOCKED = "locked"
    AT_LIMIT = "at_limit"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


KNEE_CONFIG = {
    'knee_flexion': {
        'min_angle': 0, 'max_angle': 135,
        'max_speed': 60, 'lock_angle': 5
    }
}


class KneePositionDB(Base):
    __tablename__ = 'knee_positions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    leg = Column(String(10))
    angle = Column(Float)
    is_locked = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class MovementLogDB(Base):
    __tablename__ = 'movement_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    leg = Column(String(10))
    intent = Column(String(50))
    strength = Column(Float)
    speed_modifier = Column(Float)
    angle_before = Column(Float)
    angle_after = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

knee_states = {
    'left': {
        'status': KneeStatus.IDLE, 'angle': 0.0,
        'is_locked': False, 'emergency_stop': False
    },
    'right': {
        'status': KneeStatus.IDLE, 'angle': 0.0,
        'is_locked': False, 'emergency_stop': False
    }
}


class MoveRequest(BaseModel):
    leg: str = 'both'
    intent: str
    strength: float = 0.5
    speed_modifier: float = 1.0


class LockRequest(BaseModel):
    leg: str = 'both'


app = FastAPI(title="Knee Belt System", version="2.0")


def calculate_knee_movement(intent: str, strength: float) -> float:
    base = 30 * strength
    movements = {
        'flex_knee': base, 'extend_knee': -base,
        'squat': base * 1.5, 'stand_up': -base * 2,
        'sit_down': base * 1.2, 'brake': 0
    }
    return movements.get(intent, 0)


@app.post('/move')
def move(body: MoveRequest):
    legs_to_move = (
        ['left', 'right'] if body.leg == 'both' else [body.leg]
    )
    results = {}
    session = SessionLocal()

    try:
        for current_leg in legs_to_move:
            if current_leg not in knee_states:
                continue

            state = knee_states[current_leg]
            if state['emergency_stop']:
                results[current_leg] = {'error': 'Emergency stop active'}
                continue

            angle_before = state['angle']
            state['status'] = KneeStatus.MOVING

            if state['is_locked'] and body.intent != 'stand_up':
                state['is_locked'] = False

            change = calculate_knee_movement(body.intent, body.strength)
            config = KNEE_CONFIG['knee_flexion']
            new_angle = state['angle'] + change * body.speed_modifier
            new_angle = max(config['min_angle'], min(config['max_angle'], new_angle))
            state['angle'] = new_angle

            if new_angle <= config['lock_angle']:
                state['status'] = KneeStatus.EXTENDED
                if body.intent == 'stand_up':
                    state['is_locked'] = True
                    state['status'] = KneeStatus.LOCKED
            elif new_angle >= config['max_angle'] - 5:
                state['status'] = KneeStatus.FLEXED
            else:
                state['status'] = KneeStatus.IDLE

            logger.info(
                f"Knee {current_leg}: {angle_before:.1f} -> "
                f"{new_angle:.1f} ({body.intent})"
            )

            session.add(KneePositionDB(
                leg=current_leg, angle=new_angle,
                is_locked=state['is_locked']
            ))
            session.add(MovementLogDB(
                leg=current_leg, intent=body.intent,
                strength=body.strength, speed_modifier=body.speed_modifier,
                angle_before=angle_before, angle_after=new_angle
            ))

            results[current_leg] = {
                'angle': round(new_angle, 2),
                'is_locked': state['is_locked'],
                'status': state['status'].value
            }

        session.commit()
    finally:
        session.close()

    return {'success': True, 'intent': body.intent, 'results': results}


@app.post('/lock')
def lock_knees(body: LockRequest = LockRequest()):
    legs = ['left', 'right'] if body.leg == 'both' else [body.leg]
    for leg in legs:
        if leg in knee_states:
            config = KNEE_CONFIG['knee_flexion']
            if knee_states[leg]['angle'] <= config['lock_angle'] + 10:
                knee_states[leg]['is_locked'] = True
                knee_states[leg]['status'] = KneeStatus.LOCKED
                logger.info(f"Knee {leg} locked")
    return {'success': True, 'locked_legs': legs}


@app.post('/unlock')
def unlock_knees(body: LockRequest = LockRequest()):
    legs = ['left', 'right'] if body.leg == 'both' else [body.leg]
    for leg in legs:
        if leg in knee_states:
            knee_states[leg]['is_locked'] = False
            knee_states[leg]['status'] = KneeStatus.IDLE
            logger.info(f"Knee {leg} unlocked")
    return {'success': True, 'unlocked_legs': legs}


@app.get('/status')
def get_status():
    return {
        leg: {
            'status': knee_states[leg]['status'].value,
            'angle': round(knee_states[leg]['angle'], 2),
            'is_locked': knee_states[leg]['is_locked'],
            'emergency_stop': knee_states[leg]['emergency_stop']
        }
        for leg in ['left', 'right']
    }


@app.get('/positions/{leg}')
def get_positions(leg: str):
    if leg not in knee_states:
        raise HTTPException(status_code=404, detail='Invalid leg')
    s = knee_states[leg]
    return {
        'leg': leg, 'angle': round(s['angle'], 2),
        'is_locked': s['is_locked'], 'status': s['status'].value
    }


@app.get('/movement_history')
def get_movement_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(MovementLogDB)
            .order_by(MovementLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id, 'leg': l.leg, 'intent': l.intent,
            'strength': l.strength, 'angle_before': l.angle_before,
            'angle_after': l.angle_after,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


@app.post('/emergency_stop')
def emergency_stop():
    for leg in knee_states:
        knee_states[leg]['emergency_stop'] = True
        knee_states[leg]['status'] = KneeStatus.EMERGENCY_STOP
        knee_states[leg]['is_locked'] = True
    logger.warning("EMERGENCY STOP activated")
    return {'message': 'Knee system emergency stop activated'}


@app.post('/reset')
def reset():
    for leg in knee_states:
        knee_states[leg]['emergency_stop'] = False
        knee_states[leg]['status'] = KneeStatus.IDLE
        knee_states[leg]['is_locked'] = False
    logger.info("System reset")
    return {'message': 'Knee system reset'}


@app.get('/health')
def health_check():
    return {'status': 'healthy', 'module': MODULE_NAME}


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)