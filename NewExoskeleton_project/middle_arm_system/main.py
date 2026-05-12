# middle_arm_system.py
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
PORT = 8004
MODULE_NAME = os.getenv('MODULE_NAME', 'middle_arm_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///middle_arm.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class JointStatus(str, Enum):
    IDLE = "idle"
    MOVING = "moving"
    AT_LIMIT = "at_limit"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


JOINTS_CONFIG = {
    'elbow_flexion': {
        'min_angle': 0, 'max_angle': 145, 'max_speed': 80
    },
    'forearm_pronation': {
        'min_angle': -80, 'max_angle': 80, 'max_speed': 60
    }
}


class JointPositionDB(Base):
    __tablename__ = 'joint_positions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    arm = Column(String(10))
    joint_name = Column(String(50))
    angle = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow)


class MovementLogDB(Base):
    __tablename__ = 'movement_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    arm = Column(String(10))
    intent = Column(String(50))
    strength = Column(Float)
    speed_modifier = Column(Float)
    positions_before = Column(String(500))
    positions_after = Column(String(500))
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

joint_states = {
    'left': {
        'status': JointStatus.IDLE,
        'positions': {joint: 0.0 for joint in JOINTS_CONFIG},
        'emergency_stop': False
    },
    'right': {
        'status': JointStatus.IDLE,
        'positions': {joint: 0.0 for joint in JOINTS_CONFIG},
        'emergency_stop': False
    }
}


class MoveRequest(BaseModel):
    arm: str
    intent: str
    strength: float = 0.5
    speed_modifier: float = 1.0


app = FastAPI(title="Middle Arm System", version="2.0")


def calculate_movement(intent: str, strength: float) -> dict:
    base_movement = 45 * strength
    movements = {
        'flex_elbow': {'elbow_flexion': base_movement},
        'extend_elbow': {'elbow_flexion': -base_movement},
        'extend_arm': {'elbow_flexion': -base_movement * 0.5},
        'retract_arm': {'elbow_flexion': base_movement * 0.7},
        'pronate': {'forearm_pronation': base_movement},
        'supinate': {'forearm_pronation': -base_movement}
    }
    return movements.get(intent, {})


@app.post('/move')
def move(body: MoveRequest):
    if body.arm not in joint_states:
        raise HTTPException(status_code=400, detail='Invalid arm specified')

    if joint_states[body.arm]['emergency_stop']:
        raise HTTPException(
            status_code=403, detail='Emergency stop is active'
        )

    state = joint_states[body.arm]
    positions_before = state['positions'].copy()
    state['status'] = JointStatus.MOVING

    target_changes = calculate_movement(body.intent, body.strength)

    logger.info(
        f"Moving {body.arm} arm: intent={body.intent}, "
        f"strength={body.strength}, speed={body.speed_modifier}"
    )

    session = SessionLocal()
    new_positions = {}
    try:
        for joint, change in target_changes.items():
            if joint in state['positions']:
                current = state['positions'][joint]
                config = JOINTS_CONFIG[joint]
                new_angle = current + change * body.speed_modifier
                new_angle = max(
                    config['min_angle'],
                    min(config['max_angle'], new_angle)
                )

                state['positions'][joint] = new_angle
                new_positions[joint] = new_angle

                pos_record = JointPositionDB(
                    arm=body.arm,
                    joint_name=joint,
                    angle=new_angle
                )
                session.add(pos_record)

                logger.info(
                    f"  Joint {joint}: {current:.1f} -> {new_angle:.1f}"
                )

        log_entry = MovementLogDB(
            arm=body.arm,
            intent=body.intent,
            strength=body.strength,
            speed_modifier=body.speed_modifier,
            positions_before=str(positions_before),
            positions_after=str(new_positions)
        )
        session.add(log_entry)
        session.commit()
    finally:
        session.close()

    state['status'] = JointStatus.IDLE

    return {
        'success': True,
        'arm': body.arm,
        'intent': body.intent,
        'positions': new_positions
    }


@app.get('/status')
def get_status():
    return {
        arm: {
            'status': joint_states[arm]['status'].value,
            'positions': joint_states[arm]['positions'],
            'emergency_stop': joint_states[arm]['emergency_stop']
        }
        for arm in ['left', 'right']
    }


@app.get('/positions/{arm}')
def get_positions(arm: str):
    if arm not in joint_states:
        raise HTTPException(status_code=404, detail='Invalid arm')
    return {
        'arm': arm,
        'positions': joint_states[arm]['positions'],
        'status': joint_states[arm]['status'].value
    }


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
            'id': l.id,
            'arm': l.arm,
            'intent': l.intent,
            'strength': l.strength,
            'speed_modifier': l.speed_modifier,
            'positions_before': l.positions_before,
            'positions_after': l.positions_after,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


@app.post('/emergency_stop')
def emergency_stop():
    for arm in joint_states:
        joint_states[arm]['emergency_stop'] = True
        joint_states[arm]['status'] = JointStatus.EMERGENCY_STOP
    logger.warning("EMERGENCY STOP activated")
    return {'message': 'Middle arm emergency stop activated'}


@app.post('/reset')
def reset():
    for arm in joint_states:
        joint_states[arm]['emergency_stop'] = False
        joint_states[arm]['status'] = JointStatus.IDLE
    logger.info("System reset")
    return {'message': 'Middle arm system reset'}


@app.get('/health')
def health_check():
    return {'status': 'healthy', 'module': MODULE_NAME}


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)