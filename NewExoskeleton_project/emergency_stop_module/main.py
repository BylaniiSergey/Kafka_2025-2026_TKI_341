# emergency_stop_module/main.py
import os
import logging
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, String,
    Boolean, DateTime, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5203))
MODULE_NAME = os.getenv('MODULE_NAME', 'emergency_stop_module')

ARM_MOVEMENT_URL = os.getenv(
    'ARM_MOVEMENT_URL', 'http://localhost:8002'
)
LEG_MOVEMENT_URL = os.getenv(
    'LEG_MOVEMENT_URL', 'http://localhost:9002'
)
UPPER_ARM_URL = os.getenv(
    'UPPER_ARM_URL', 'http://localhost:8003'
)
MIDDLE_ARM_URL = os.getenv(
    'MIDDLE_ARM_URL', 'http://localhost:8004'
)
FINGERS_URL = os.getenv(
    'FINGERS_URL', 'http://localhost:8005'
)
FORCE_CONTROL_URL = os.getenv(
    'FORCE_CONTROL_URL', 'http://localhost:8006'
)
KNEE_BELT_URL = os.getenv(
    'KNEE_BELT_URL', 'http://localhost:9003'
)
TRACK_SYSTEM_URL = os.getenv(
    'TRACK_SYSTEM_URL', 'http://localhost:9004'
)
LEG_FORCE_URL = os.getenv(
    'LEG_FORCE_URL', 'http://localhost:9006'
)
REQUEST_TIMEOUT = 5.0

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

# Все приводные подсистемы для финальной остановки
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

# Подсистемы, которые нужно освободить ДО безопасной позы
RELEASE_FIRST = {
    'fingers': FINGERS_URL,
    'force_control': FORCE_CONTROL_URL,
    'track_system': TRACK_SYSTEM_URL,
    'leg_force': LEG_FORCE_URL,
}

# Параметры безопасной позы
SAFE_POSE_ARMS = {
    'arm': 'both',
    'intent': 'lower_arm',
    'strength': 0.3,
    'speed_modifier': 0.5
}
SAFE_POSE_LEGS = {
    'leg': 'both',
    'intent': 'stand_up',
    'strength': 0.3,
    'speed_modifier': 0.5
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
    phase_1_released = Column(Text, nullable=True)
    phase_1_failed = Column(Text, nullable=True)
    phase_2_arm_pose = Column(Boolean, default=False)
    phase_2_leg_pose = Column(Boolean, default=False)
    phase_3_stopped = Column(Text, nullable=True)
    phase_3_failed = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

module_state = {
    'safe_pose_active': False,
    'total_activations': 0,
    'last_reason': None,
    'last_source': None,
    'last_phases': {}
}


class SafePoseRequest(BaseModel):
    source: str = 'emergency_control_module'
    reason: str = 'emergency'


app = FastAPI(title="Emergency Stop Module", version="1.2")


def _try_post(url: str, endpoint: str, json_data: dict = None) -> bool:
    """Попытка POST-запроса с обработкой ошибок."""
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(
                f'{url}/{endpoint}',
                json=json_data or {}
            )
            return resp.status_code in [200, 204]
    except Exception as e:
        logger.error(f"POST {url}/{endpoint} failed: {e}")
        return False


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def get_status():
    return {
        'service': MODULE_NAME,
        'safe_pose_active': module_state['safe_pose_active'],
        'total_activations': module_state['total_activations'],
        'last_reason': module_state['last_reason'],
        'last_source': module_state['last_source'],
        'last_phases': module_state['last_phases']
    }


@app.post('/safe_pose')
def apply_safe_pose(body: SafePoseRequest):
    """
    Принудительное приведение экзоскелета в безопасную позу.

    Порядок критически важен:
      Фаза 1: Освобождение захватов, тяги, силовых приводов
               (чтобы не было сопротивления при смене позы)
      Фаза 2: Перевод рук и ног в безопасное положение
               (руки вниз, ноги в стойку)
      Фаза 3: Финальная аварийная остановка всех приводов
               (фиксация в безопасной позе)
    """
    logger.critical(
        f"=== SAFE POSE SEQUENCE ==="
        f" source='{body.source}', reason='{body.reason}'"
    )

    module_state['safe_pose_active'] = True
    module_state['total_activations'] += 1
    module_state['last_reason'] = body.reason
    module_state['last_source'] = body.source

    # ========================================
    # ФАЗА 1: Освобождение захватов и тяги
    # ========================================
    logger.info("--- Phase 1: Release grips & traction ---")

    phase1_released = []
    phase1_failed = []

    for name, url in RELEASE_FIRST.items():
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
                # Пытаемся /release (fingers, force_control, leg_force)
                resp = c.post(
                    f'{url}/release',
                    json={}
                )
                if resp.status_code in [200, 204]:
                    phase1_released.append(name)
                    logger.info(f"  Released: {name}")
                else:
                    phase1_failed.append(
                        f"{name}:HTTP{resp.status_code}"
                    )
                    logger.warning(
                        f"  Release {name}: "
                        f"HTTP {resp.status_code}"
                    )
        except Exception as e:
            phase1_failed.append(f"{name}:{e}")
            logger.error(f"  Release {name} failed: {e}")

    # Дополнительно: остановить гусеницы плавно
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(
                f'{TRACK_SYSTEM_URL}/move',
                json={
                    'leg': 'both',
                    'intent': 'brake',
                    'strength': 1.0,
                    'speed_modifier': 1.0
                }
            )
            if resp.status_code in [200, 204]:
                phase1_released.append('track_brake')
                logger.info("  Track braking applied")
            else:
                phase1_failed.append(
                    f"track_brake:HTTP{resp.status_code}"
                )
    except Exception as e:
        phase1_failed.append(f"track_brake:{e}")
        logger.error(f"  Track brake failed: {e}")

    logger.info(
        f"Phase 1 complete: released={phase1_released}, "
        f"failed={phase1_failed}"
    )

    # ========================================
    # ФАЗА 2: Безопасная поза
    # ========================================
    logger.info("--- Phase 2: Safe pose ---")

    # Сначала нужно временно снять emergency_stop
    # на arm_movement и leg_movement, чтобы они могли
    # принять команду safe pose
    for name, url in {
        'arm_movement': ARM_MOVEMENT_URL,
        'leg_movement': LEG_MOVEMENT_URL
    }.items():
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
                c.post(f'{url}/reset')
                logger.info(f"  Temp reset for safe pose: {name}")
        except Exception as e:
            logger.error(f"  Temp reset {name} failed: {e}")

    # Руки вниз
    arm_pose_ok = False
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(
                f'{ARM_MOVEMENT_URL}/execute',
                json=SAFE_POSE_ARMS
            )
            arm_pose_ok = resp.status_code in [200, 204]
            if arm_pose_ok:
                logger.info("  Arm safe pose: OK")
            else:
                logger.warning(
                    f"  Arm safe pose: HTTP {resp.status_code}"
                )
    except Exception as e:
        logger.error(f"  Arm safe pose failed: {e}")

    # Ноги в стойку
    leg_pose_ok = False
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(
                f'{LEG_MOVEMENT_URL}/execute',
                json=SAFE_POSE_LEGS
            )
            leg_pose_ok = resp.status_code in [200, 204]
            if leg_pose_ok:
                logger.info("  Leg safe pose: OK")
            else:
                logger.warning(
                    f"  Leg safe pose: HTTP {resp.status_code}"
                )
    except Exception as e:
        logger.error(f"  Leg safe pose failed: {e}")

    logger.info(
        f"Phase 2 complete: arm_pose={arm_pose_ok}, "
        f"leg_pose={leg_pose_ok}"
    )

    # ========================================
    # ФАЗА 3: Финальная аварийная остановка
    # ========================================
    logger.info("--- Phase 3: Final emergency stop ---")

    phase3_stopped = []
    phase3_failed = []

    for name, url in ALL_DRIVE_SUBSYSTEMS.items():
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
                resp = c.post(f'{url}/emergency_stop')
                if resp.status_code in [200, 204]:
                    phase3_stopped.append(name)
                    logger.info(f"  Stopped: {name}")
                else:
                    phase3_failed.append(
                        f"{name}:HTTP{resp.status_code}"
                    )
                    logger.warning(
                        f"  Stop {name}: "
                        f"HTTP {resp.status_code}"
                    )
        except Exception as e:
            phase3_failed.append(f"{name}:{e}")
            logger.error(f"  Stop {name} failed: {e}")

    logger.info(
        f"Phase 3 complete: stopped={phase3_stopped}, "
        f"failed={phase3_failed}"
    )

    # ========================================
    # Сохранение результата
    # ========================================
    phases = {
        'phase_1': {
            'released': phase1_released,
            'failed': phase1_failed
        },
        'phase_2': {
            'arm_pose': arm_pose_ok,
            'leg_pose': leg_pose_ok
        },
        'phase_3': {
            'stopped': phase3_stopped,
            'failed': phase3_failed
        }
    }
    module_state['last_phases'] = phases

    session = SessionLocal()
    try:
        session.add(SafePoseEventDB(
            source=body.source,
            reason=body.reason,
            phase_1_released=','.join(phase1_released),
            phase_1_failed=','.join(phase1_failed),
            phase_2_arm_pose=arm_pose_ok,
            phase_2_leg_pose=leg_pose_ok,
            phase_3_stopped=','.join(phase3_stopped),
            phase_3_failed=','.join(phase3_failed)
        ))
        session.commit()
    finally:
        session.close()

    logger.critical(
        f"=== SAFE POSE COMPLETE === "
        f"arms={arm_pose_ok}, legs={leg_pose_ok}, "
        f"drives_stopped={len(phase3_stopped)}/"
        f"{len(ALL_DRIVE_SUBSYSTEMS)}"
    )

    return {
        'ok': True,
        'safe_pose_active': True,
        'source': body.source,
        'reason': body.reason,
        'phases': phases
    }


@app.post('/reset')
def reset(source: str = 'operator'):
    """Сбросить аварийный режим и разблокировать приводы."""
    logger.info(f"Safe pose reset by {source}")

    module_state['safe_pose_active'] = False

    reset_results = {}
    for name, url in ALL_DRIVE_SUBSYSTEMS.items():
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
                resp = c.post(f'{url}/reset')
                reset_results[name] = (
                    resp.status_code in [200, 204]
                )
        except Exception as e:
            reset_results[name] = False
            logger.error(f"Reset {name} failed: {e}")

    return {
        'ok': True,
        'safe_pose_active': False,
        'reset_by': source,
        'subsystem_resets': reset_results
    }


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
            'phase_1_released': e.phase_1_released,
            'phase_1_failed': e.phase_1_failed,
            'phase_2_arm_pose': e.phase_2_arm_pose,
            'phase_2_leg_pose': e.phase_2_leg_pose,
            'phase_3_stopped': e.phase_3_stopped,
            'phase_3_failed': e.phase_3_failed,
            'created_at': e.created_at.strftime(
                '%Y-%m-%d %H:%M:%S'
            ) if e.created_at else None
        } for e in events]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)