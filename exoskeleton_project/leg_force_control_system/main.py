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
PORT = 9006
MODULE_NAME = os.getenv('MODULE_NAME', 'leg_force_control_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///leg_force_control.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class ForceStatus(str, Enum):
    IDLE = "idle"
    APPLYING = "applying"
    SUPPORTING = "supporting"
    DRIVING = "driving"
    RELEASING = "releasing"
    OVERLOAD = "overload"
    EMERGENCY_STOP = "emergency_stop"


class ForceReadingDB(Base):
    __tablename__ = 'force_readings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    location = Column(String(20))
    force_value = Column(Float)
    torque_value = Column(Float)
    timestamp = Column(DateTime, default=datetime.utcnow)


class ForceLogDB(Base):
    __tablename__ = 'force_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    location = Column(String(20))
    action = Column(String(50))
    target_value = Column(Float)
    applied_value = Column(Float)
    status_before = Column(String(30))
    status_after = Column(String(30))
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

SAFETY_THRESHOLDS = {
    'knee': {
        'max_safe_torque': 150.0, 'emergency_threshold': 200.0,
        'standing_torque': 80.0, 'squat_torque': 120.0
    },
    'track': {
        'max_safe_force': 500.0, 'emergency_threshold': 800.0,
        'normal_load': 300.0
    }
}

force_state = {
    'left_knee': {
        'status': ForceStatus.IDLE,
        'current_torque': 0.0, 'emergency_stop': False
    },
    'right_knee': {
        'status': ForceStatus.IDLE,
        'current_torque': 0.0, 'emergency_stop': False
    },
    'left_track': {
        'status': ForceStatus.IDLE,
        'current_force': 0.0, 'traction': 0.0, 'emergency_stop': False
    },
    'right_track': {
        'status': ForceStatus.IDLE,
        'current_force': 0.0, 'traction': 0.0, 'emergency_stop': False
    }
}


class KneeTorqueRequest(BaseModel):
    leg: str = 'both'
    action: str = 'support'
    target_torque: float = 80.0


class TrackForceRequest(BaseModel):
    track: str = 'both'
    target_force: float = 300.0
    traction_mode: str = 'normal'


class ReleaseRequest(BaseModel):
    location: str = None


app = FastAPI(title="Leg Force Control System", version="2.0")


@app.post('/apply_knee_torque')
def apply_knee_torque(body: KneeTorqueRequest):
    legs = (
        ['left_knee', 'right_knee']
        if body.leg == 'both' else [f'{body.leg}_knee']
    )
    results = {}
    session = SessionLocal()

    try:
        for knee in legs:
            if knee not in force_state:
                continue
            state = force_state[knee]
            if state['emergency_stop']:
                results[knee] = {'error': 'Emergency stop active'}
                continue

            status_before = state['status'].value
            state['status'] = ForceStatus.APPLYING
            safe = min(
                body.target_torque,
                SAFETY_THRESHOLDS['knee']['max_safe_torque']
            )

            if body.target_torque > SAFETY_THRESHOLDS['knee']['emergency_threshold']:
                state['status'] = ForceStatus.OVERLOAD
                safe = SAFETY_THRESHOLDS['knee']['max_safe_torque']
                logger.warning(f"OVERLOAD on {knee}")

            state['current_torque'] = safe
            state['status'] = ForceStatus.SUPPORTING

            session.add(ForceReadingDB(
                location=knee, force_value=0, torque_value=safe
            ))
            session.add(ForceLogDB(
                location=knee, action=body.action,
                target_value=body.target_torque, applied_value=safe,
                status_before=status_before,
                status_after=state['status'].value
            ))

            logger.info(f"Knee torque {knee}: {safe:.1f}")
            results[knee] = {
                'torque': round(safe, 2),
                'status': state['status'].value
            }

        session.commit()
    finally:
        session.close()

    return {'success': True, 'action': body.action, 'results': results}


@app.post('/apply_track_force')
def apply_track_force(body: TrackForceRequest):
    tracks = (
        ['left_track', 'right_track']
        if body.track == 'both' else [f'{body.track}_track']
    )
    results = {}
    session = SessionLocal()

    try:
        for track_name in tracks:
            if track_name not in force_state:
                continue
            state = force_state[track_name]
            if state['emergency_stop']:
                results[track_name] = {'error': 'Emergency stop active'}
                continue

            status_before = state['status'].value
            state['status'] = ForceStatus.APPLYING
            safe = min(
                body.target_force,
                SAFETY_THRESHOLDS['track']['max_safe_force']
            )

            traction_map = {
                'high_grip': 0.9, 'low_friction': 0.5, 'normal': 0.7
            }
            traction = safe * traction_map.get(body.traction_mode, 0.7)

            state['current_force'] = safe
            state['traction'] = traction
            state['status'] = ForceStatus.DRIVING

            session.add(ForceReadingDB(
                location=track_name, force_value=safe, torque_value=0
            ))
            session.add(ForceLogDB(
                location=track_name, action='track_force',
                target_value=body.target_force, applied_value=safe,
                status_before=status_before,
                status_after=state['status'].value
            ))

            logger.info(f"Track force {track_name}: {safe:.1f}")
            results[track_name] = {
                'force': round(safe, 2), 'traction': round(traction, 2),
                'status': state['status'].value
            }

        session.commit()
    finally:
        session.close()

    return {
        'success': True, 'traction_mode': body.traction_mode,
        'results': results
    }


@app.post('/release')
def release(body: ReleaseRequest = ReleaseRequest()):
    if body.location == 'all' or body.location is None:
        locations = list(force_state.keys())
    else:
        locations = (
            [body.location] if body.location in force_state else []
        )

    for loc in locations:
        state = force_state[loc]
        state['status'] = ForceStatus.IDLE
        if 'knee' in loc:
            state['current_torque'] = 0.0
        else:
            state['current_force'] = 0.0
            state['traction'] = 0.0
        logger.info(f"Released: {loc}")

    return {'success': True, 'released': locations}


@app.get('/status')
def get_status():
    return {
        loc: {
            'status': force_state[loc]['status'].value,
            'current_torque': round(
                force_state[loc].get('current_torque', 0), 2
            ),
            'current_force': round(
                force_state[loc].get('current_force', 0), 2
            ),
            'traction': round(
                force_state[loc].get('traction', 0), 2
            ),
            'emergency_stop': force_state[loc]['emergency_stop']
        }
        for loc in force_state
    }


@app.get('/thresholds')
def get_thresholds():
    return SAFETY_THRESHOLDS


@app.get('/force_history')
def get_force_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(ForceLogDB)
            .order_by(ForceLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id, 'location': l.location, 'action': l.action,
            'target_value': l.target_value,
            'applied_value': l.applied_value,
            'status_before': l.status_before,
            'status_after': l.status_after,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


@app.post('/emergency_stop')
def emergency_stop():
    for loc in force_state:
        force_state[loc]['emergency_stop'] = True
        force_state[loc]['status'] = ForceStatus.EMERGENCY_STOP
        if 'knee' in loc:
            force_state[loc]['current_torque'] = 0.0
        else:
            force_state[loc]['current_force'] = 0.0
            force_state[loc]['traction'] = 0.0
    logger.warning("EMERGENCY STOP activated")
    return {'message': 'Leg force control emergency stop activated'}


@app.post('/reset')
def reset():
    for loc in force_state:
        force_state[loc]['emergency_stop'] = False
        force_state[loc]['status'] = ForceStatus.IDLE
        if 'knee' in loc:
            force_state[loc]['current_torque'] = 0.0
        else:
            force_state[loc]['current_force'] = 0.0
            force_state[loc]['traction'] = 0.0
    logger.info("System reset")
    return {'message': 'Leg force control reset'}


@app.get('/health')
def health_check():
    return {'status': 'healthy', 'module': MODULE_NAME}


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)