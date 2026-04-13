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
PORT = 9004
MODULE_NAME = os.getenv('MODULE_NAME', 'track_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///track_system.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class TrackStatus(str, Enum):
    IDLE = "idle"
    MOVING_FORWARD = "moving_forward"
    MOVING_BACKWARD = "moving_backward"
    TURNING = "turning"
    PIVOTING = "pivoting"
    BRAKING = "braking"
    STOPPED = "stopped"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


class DriveMode(str, Enum):
    NORMAL = "normal"
    SLOW = "slow"
    FAST = "fast"
    TERRAIN = "terrain"
    INDOOR = "indoor"


TRACK_CONFIG = {
    'max_speed': 5.0, 'max_reverse_speed': 3.0,
    'acceleration': 0.5, 'deceleration': 1.0,
    'turn_radius_min': 0.5, 'track_width': 0.4, 'track_length': 0.6
}


class TrackTelemetryDB(Base):
    __tablename__ = 'track_telemetry'

    id = Column(Integer, primary_key=True, autoincrement=True)
    left_speed = Column(Float)
    right_speed = Column(Float)
    direction = Column(String(20))
    mode = Column(String(20))
    timestamp = Column(DateTime, default=datetime.utcnow)


class MovementLogDB(Base):
    __tablename__ = 'movement_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    intent = Column(String(50))
    left_speed_before = Column(Float)
    left_speed_after = Column(Float)
    right_speed_before = Column(Float)
    right_speed_after = Column(Float)
    status = Column(String(30))
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

track_state = {
    'left_track': {
        'speed': 0.0, 'target_speed': 0.0,
        'direction': 'stopped', 'motor_load': 0.0
    },
    'right_track': {
        'speed': 0.0, 'target_speed': 0.0,
        'direction': 'stopped', 'motor_load': 0.0
    },
    'status': TrackStatus.IDLE,
    'mode': DriveMode.NORMAL,
    'emergency_stop': False,
    'odometer': 0.0
}


class MoveRequest(BaseModel):
    leg: str = 'both'
    intent: str
    strength: float = 0.5
    speed_modifier: float = 1.0


class SpeedRequest(BaseModel):
    left_speed: float = 0.0
    right_speed: float = 0.0


class ModeRequest(BaseModel):
    mode: str = 'normal'


app = FastAPI(title="Track System", version="2.0")


def get_direction(speed: float) -> str:
    if speed > 0.01:
        return 'forward'
    elif speed < -0.01:
        return 'backward'
    return 'stopped'


def calculate_track_speeds(intent, strength, speed_modifier):
    max_spd = TRACK_CONFIG['max_speed'] * strength * speed_modifier / 3.6
    max_rev = TRACK_CONFIG['max_reverse_speed'] * strength * speed_modifier / 3.6

    mapping = {
        'move_forward': (max_spd, max_spd, TrackStatus.MOVING_FORWARD),
        'move_backward': (-max_rev, -max_rev, TrackStatus.MOVING_BACKWARD),
        'turn_left': (max_spd * 0.3, max_spd, TrackStatus.TURNING),
        'turn_right': (max_spd, max_spd * 0.3, TrackStatus.TURNING),
        'pivot_left': (-max_spd * 0.5, max_spd * 0.5, TrackStatus.PIVOTING),
        'pivot_right': (max_spd * 0.5, -max_spd * 0.5, TrackStatus.PIVOTING),
        'stop': (0.0, 0.0, TrackStatus.STOPPED),
        'brake': (0.0, 0.0, TrackStatus.BRAKING),
    }
    return mapping.get(intent, (0.0, 0.0, TrackStatus.IDLE))


@app.post('/move')
def move(body: MoveRequest):
    if track_state['emergency_stop']:
        raise HTTPException(
            status_code=403, detail='Emergency stop is active'
        )

    left_before = track_state['left_track']['speed']
    right_before = track_state['right_track']['speed']

    left_speed, right_speed, status = calculate_track_speeds(
        body.intent, body.strength, body.speed_modifier
    )

    track_state['left_track']['speed'] = left_speed
    track_state['left_track']['target_speed'] = left_speed
    track_state['left_track']['direction'] = get_direction(left_speed)
    track_state['right_track']['speed'] = right_speed
    track_state['right_track']['target_speed'] = right_speed
    track_state['right_track']['direction'] = get_direction(right_speed)
    track_state['status'] = status

    logger.info(
        f"Track move: {body.intent}, L={left_speed:.2f}, R={right_speed:.2f}"
    )

    session = SessionLocal()
    try:
        session.add(TrackTelemetryDB(
            left_speed=left_speed, right_speed=right_speed,
            direction=body.intent, mode=track_state['mode'].value
        ))
        session.add(MovementLogDB(
            intent=body.intent,
            left_speed_before=left_before, left_speed_after=left_speed,
            right_speed_before=right_before, right_speed_after=right_speed,
            status=status.value
        ))
        session.commit()
    finally:
        session.close()

    return {
        'success': True, 'intent': body.intent,
        'left_track': {
            'speed': round(left_speed, 2),
            'direction': track_state['left_track']['direction']
        },
        'right_track': {
            'speed': round(right_speed, 2),
            'direction': track_state['right_track']['direction']
        },
        'status': status.value
    }


@app.post('/set_mode')
def set_mode(body: ModeRequest):
    try:
        track_state['mode'] = DriveMode(body.mode)
        logger.info(f"Drive mode set to: {body.mode}")
        return {'success': True, 'mode': track_state['mode'].value}
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f'Unknown mode: {body.mode}'
        )


@app.post('/speed')
def set_speed(body: SpeedRequest):
    if track_state['emergency_stop']:
        raise HTTPException(
            status_code=403, detail='Emergency stop is active'
        )

    max_spd = TRACK_CONFIG['max_speed'] / 3.6
    ls = max(-max_spd, min(max_spd, body.left_speed))
    rs = max(-max_spd, min(max_spd, body.right_speed))

    track_state['left_track']['speed'] = ls
    track_state['left_track']['direction'] = get_direction(ls)
    track_state['right_track']['speed'] = rs
    track_state['right_track']['direction'] = get_direction(rs)

    if ls == 0 and rs == 0:
        track_state['status'] = TrackStatus.STOPPED
    elif ls == rs:
        track_state['status'] = (
            TrackStatus.MOVING_FORWARD if ls > 0
            else TrackStatus.MOVING_BACKWARD
        )
    else:
        track_state['status'] = TrackStatus.TURNING

    return {
        'success': True, 'left_speed': round(ls, 2),
        'right_speed': round(rs, 2),
        'status': track_state['status'].value
    }


@app.get('/status')
def get_status():
    return {
        'status': track_state['status'].value,
        'mode': track_state['mode'].value,
        'emergency_stop': track_state['emergency_stop'],
        'left_track': track_state['left_track'],
        'right_track': track_state['right_track'],
        'odometer': round(track_state['odometer'], 2),
        'config': TRACK_CONFIG
    }


@app.get('/telemetry')
def get_telemetry(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        records = (
            session.query(TrackTelemetryDB)
            .order_by(TrackTelemetryDB.timestamp.desc())
            .limit(limit).all()
        )
        return [{
            'id': r.id, 'left_speed': r.left_speed,
            'right_speed': r.right_speed, 'direction': r.direction,
            'mode': r.mode,
            'timestamp': r.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                if r.timestamp else None
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
            .limit(limit).all()
        )
        return [{
            'id': l.id, 'intent': l.intent,
            'left_speed_before': l.left_speed_before,
            'left_speed_after': l.left_speed_after,
            'right_speed_before': l.right_speed_before,
            'right_speed_after': l.right_speed_after,
            'status': l.status,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


@app.post('/emergency_stop')
def emergency_stop():
    track_state['emergency_stop'] = True
    track_state['status'] = TrackStatus.EMERGENCY_STOP
    for t in ['left_track', 'right_track']:
        track_state[t]['speed'] = 0.0
        track_state[t]['target_speed'] = 0.0
        track_state[t]['direction'] = 'stopped'
    logger.warning("EMERGENCY STOP activated")
    return {'message': 'Track emergency stop activated'}


@app.post('/reset')
def reset():
    track_state['emergency_stop'] = False
    track_state['status'] = TrackStatus.IDLE
    track_state['mode'] = DriveMode.NORMAL
    for t in ['left_track', 'right_track']:
        track_state[t]['speed'] = 0.0
        track_state[t]['target_speed'] = 0.0
        track_state[t]['direction'] = 'stopped'
    logger.info("System reset")
    return {'message': 'Track system reset'}


@app.get('/health')
def health_check():
    return {'status': 'healthy', 'module': MODULE_NAME}


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)