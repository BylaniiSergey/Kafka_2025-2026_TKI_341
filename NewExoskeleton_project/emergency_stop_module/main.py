# emergency_stop_module/main.py
import os
import logging
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5003))
MODULE_NAME = os.getenv('MODULE_NAME', 'emergency_stop_module')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

# Подсистемы движения
ARM_MOVEMENT_URL = os.getenv('ARM_MOVEMENT_URL', 'http://localhost:8002')
LEG_MOVEMENT_URL = os.getenv('LEG_MOVEMENT_URL', 'http://localhost:9002')
UPPER_ARM_URL = os.getenv('UPPER_ARM_URL', 'http://localhost:8003')
MIDDLE_ARM_URL = os.getenv('MIDDLE_ARM_URL', 'http://localhost:8004')
FINGERS_URL = os.getenv('FINGERS_URL', 'http://localhost:8005')
FORCE_CONTROL_URL = os.getenv('FORCE_CONTROL_URL', 'http://localhost:8006')
KNEE_BELT_URL = os.getenv('KNEE_BELT_URL', 'http://localhost:9003')
TRACK_SYSTEM_URL = os.getenv('TRACK_SYSTEM_URL', 'http://localhost:9004')
LEG_FORCE_URL = os.getenv('LEG_FORCE_URL', 'http://localhost:9006')

REQUEST_TIMEOUT = 5.0

# Безопасная поза — углы суставов
SAFE_POSE = {
    'arms': {
        'intent': 'lower_arm',
        'strength': 0.3,
        'speed_modifier': 0.5
    },
    'legs': {
        'intent': 'stand_up',
        'strength': 0.3,
        'speed_modifier': 0.5
    }
}

ALL_DRIVE_SUBSYSTEMS = {
    'arm_movement': ARM_MOVEMENT_URL,
    'upper_arm': UPPER_ARM_URL,
    'middle_arm': MIDDLE_ARM_URL,
    'fingers': FINGERS_URL,
    'force_control': FORCE_CONTROL_URL,
    'leg_movement': LEG_MOVEMENT_URL,
    'knee_belt': KNEE_BELT_URL,
    'track_system': TRACK_SYSTEM_URL,
    'leg_force': LEG_FORCE_URL,
}

DATABASE_URL = 'sqlite:///emergency_stop_module.db'
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class SafePoseEventDB(Base):
    __tablename__ = 'safe_pose_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(100))
    reason = Column(Text)
    subsystems_stopped = Column(Text)
    subsystems_failed = Column(Text)
    pose_applied = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

module_state = {
    'safe_pose_active': False,
    'total_activations': 0,
    'last_reason': None
}


class SafePoseRequest(BaseModel):
    source: str = 'emergency_control_module'
    reason: str = 'emergency'


app = FastAPI(title="Emergency Stop Module", version="1.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def get_status():
    return {
        'service': MODULE_NAME,
        'safe_pose_active': module_state['safe_pose_active'],
        'total_activations': module_state['total_activations'],
        'last_reason': module_state['last_reason']
    }


@app.post('/safe_pose')
def apply_safe_pose(body: SafePoseRequest):
    """
    Принудительное приведение экзоскелета в безопасную позу.
    Шаг 1: Остановить все приводы через /emergency_stop.
    Шаг 2: Перевести руки в 'lower_arm', ноги в 'stand_up'.
    """
    logger.critical(
        f"SAFE POSE: source='{body.source}', reason='{body.reason}'"
    )

    module_state['safe_pose_active'] = True
    module_state['total_activations'] += 1
    module_state['last_reason'] = body.reason

    stopped_ok = []
    stopped_fail = []

    # Шаг 1: emergency_stop на все приводы
    for name, url in ALL_DRIVE_SUBSYSTEMS.items():
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
                resp = c.post(f'{url}/emergency_stop')
                if resp.status_code in [200, 204]:
                    stopped_ok.append(name)
                else:
                    stopped_fail.append(
                        f"{name}:HTTP{resp.status_code}"
                    )
        except Exception as e:
            stopped_fail.append(f"{name}:{e}")
            logger.error(f"Stop failed for {name}: {e}")

    logger.info(
        f"Emergency stop sent: ok={stopped_ok}, fail={stopped_fail}"
    )

    # Шаг 2: Безопасная поза рук
    pose_applied = False
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            c.post(
                f'{ARM_MOVEMENT_URL}/execute',
                json={
                    'arm': 'both',
                    **SAFE_POSE['arms']
                }
            )
            logger.info("Safe arm pose applied")
            pose_applied = True
    except Exception as e:
        logger.error(f"Arm safe pose failed: {e}")

    # Шаг 2: Безопасная поза ног
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            c.post(
                f'{LEG_MOVEMENT_URL}/execute',
                json={
                    'leg': 'both',
                    **SAFE_POSE['legs']
                }
            )
            logger.info("Safe leg pose applied")
    except Exception as e:
        logger.error(f"Leg safe pose failed: {e}")

    # Сохранить
    session = SessionLocal()
    try:
        session.add(SafePoseEventDB(
            source=body.source,
            reason=body.reason,
            subsystems_stopped=','.join(stopped_ok),
            subsystems_failed=','.join(stopped_fail),
            pose_applied=pose_applied
        ))
        session.commit()
    finally:
        session.close()

    return {
        'ok': True,
        'safe_pose_active': True,
        'stopped_subsystems': stopped_ok,
        'failed_subsystems': stopped_fail,
        'pose_applied': pose_applied,
        'source': body.source,
        'reason': body.reason
    }


@app.post('/reset')
def reset(source: str = 'operator'):
    module_state['safe_pose_active'] = False
    logger.info(f"Safe pose reset by {source}")
    return {'ok': True, 'safe_pose_active': False}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        events = (
            session.query(SafePoseEventDB)
            .order_by(SafePoseEventDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': e.id,
            'source': e.source,
            'reason': e.reason,
            'subsystems_stopped': e.subsystems_stopped,
            'subsystems_failed': e.subsystems_failed,
            'pose_applied': e.pose_applied,
            'created_at': e.created_at.strftime('%Y-%m-%d %H:%M:%S')
            if e.created_at else None
        } for e in events]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)