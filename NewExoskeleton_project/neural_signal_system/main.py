# neural_signal_system/main.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 8001))
MODULE_NAME = os.getenv('MODULE_NAME', 'neural_signal_system')

NEURAL_VERIFY_URL = os.getenv(
    "NEURAL_VERIFY_UPPER_URL", "http://localhost:7103"
)
REQUEST_TIMEOUT = 5.0

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///neural_signals.db'
engine       = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()


# ── Перечисления ──────────────────────────────────────────────────────────────

class TargetArm(str, Enum):
    RIGHT = "right"
    LEFT  = "left"
    BOTH  = "both"
    NONE  = "none"


class MovementIntent(str, Enum):
    LIFT_ARM    = "lift_arm"
    LOWER_ARM   = "lower_arm"
    EXTEND_ARM  = "extend_arm"
    RETRACT_ARM = "retract_arm"
    FLEX_ELBOW  = "flex_elbow"
    EXTEND_ELBOW = "extend_elbow"
    GRASP       = "grasp"
    RELEASE     = "release"
    IDLE        = "idle"


# ── БД ────────────────────────────────────────────────────────────────────────

class SignalReadingDB(Base):
    __tablename__ = 'signal_readings'
    id                        = Column(Integer, primary_key=True, autoincrement=True)
    eeg_c3                    = Column(Float, default=0.0)
    eeg_c4                    = Column(Float, default=0.0)
    stump_right_front         = Column(Float, default=0.0)
    stump_right_back          = Column(Float, default=0.0)
    stump_left_front          = Column(Float, default=0.0)
    stump_left_back           = Column(Float, default=0.0)
    shoulder_right_trapezius  = Column(Float, default=0.0)
    shoulder_right_deltoid    = Column(Float, default=0.0)
    shoulder_left_trapezius   = Column(Float, default=0.0)
    shoulder_left_deltoid     = Column(Float, default=0.0)
    chest_right               = Column(Float, default=0.0)
    chest_left                = Column(Float, default=0.0)
    detected_arm              = Column(String(20))
    detected_intent           = Column(String(50))
    strength                  = Column(Float)
    forwarded                 = Column(String(10), default='no')
    created_at                = Column(DateTime, default=datetime.utcnow)


class MovementLogDB(Base):
    __tablename__ = 'movement_log'
    id             = Column(Integer, primary_key=True, autoincrement=True)
    arm            = Column(String(20))
    intent         = Column(String(50))
    strength       = Column(Float)
    speed_modifier = Column(Float)
    source         = Column(String(50), default='neural_analysis')
    created_at     = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

VALID_SENSOR_NAMES = {
    'eeg_c3', 'eeg_c4',
    'stump_right_front', 'stump_right_back',
    'stump_left_front',  'stump_left_back',
    'shoulder_right_trapezius', 'shoulder_right_deltoid',
    'shoulder_left_trapezius',  'shoulder_left_deltoid',
    'chest_right', 'chest_left',
}


# ── Pydantic модели ───────────────────────────────────────────────────────────

class SignalsInput(BaseModel):
    signals:          Optional[Dict[str, float]] = None
    patient_id:       str  = ""
    posture:          str  = "standing"
    forward_to_verify: bool = True


# ── HTTP-клиент (патчится в тестах) ──────────────────────────────────────────

def get_client() -> httpx.Client:
    """
    Фабрика HTTP-клиента.
    Патчится в тестах через patch.object(mod, 'get_client', ...).
    """
    return httpx.Client(timeout=REQUEST_TIMEOUT)


# ── Анализ сигналов ───────────────────────────────────────────────────────────

def normalize_signal(
    value:     float,
    baseline:  float = 10.0,
    max_value: float = 150.0,
) -> float:
    if max_value <= baseline:
        return 0.0
    normalized = (value - baseline) / (max_value - baseline)
    return max(0.0, min(1.0, normalized))


def analyze_eeg_signals(signals: Dict[str, float]) -> Dict[str, Any]:
    c3        = normalize_signal(signals.get('eeg_c3', 0), 0, 100)
    c4        = normalize_signal(signals.get('eeg_c4', 0), 0, 100)
    threshold = 0.3
    target    = TargetArm.NONE

    if c3 > threshold and c4 > threshold:
        target = TargetArm.BOTH
    elif c3 > threshold and c3 > c4:
        target = TargetArm.RIGHT
    elif c4 > threshold and c4 > c3:
        target = TargetArm.LEFT

    return {
        'target_arm':    target,
        'c3_activation': round(c3, 3),
        'c4_activation': round(c4, 3),
    }


def analyze_phantom_signals(
    signals:    Dict[str, float],
    target_arm: TargetArm,
) -> Dict[str, Any]:
    if target_arm == TargetArm.NONE:
        return {'intent': MovementIntent.IDLE}

    if target_arm == TargetArm.RIGHT:
        sf = signals.get('stump_right_front', 0)
        sb = signals.get('stump_right_back',  0)
        tr = signals.get('shoulder_right_trapezius', 0)
        dl = signals.get('shoulder_right_deltoid',   0)
    elif target_arm == TargetArm.LEFT:
        sf = signals.get('stump_left_front', 0)
        sb = signals.get('stump_left_back',  0)
        tr = signals.get('shoulder_left_trapezius', 0)
        dl = signals.get('shoulder_left_deltoid',   0)
    else:
        sf = max(
            signals.get('stump_right_front', 0),
            signals.get('stump_left_front',  0),
        )
        sb = max(
            signals.get('stump_right_back', 0),
            signals.get('stump_left_back',  0),
        )
        tr = max(
            signals.get('shoulder_right_trapezius', 0),
            signals.get('shoulder_left_trapezius',  0),
        )
        dl = max(
            signals.get('shoulder_right_deltoid', 0),
            signals.get('shoulder_left_deltoid',  0),
        )

    nsf = normalize_signal(sf)
    nsb = normalize_signal(sb)
    ntr = normalize_signal(tr)
    ndl = normalize_signal(dl)

    intent = MovementIntent.IDLE
    if ntr > 0.5 and ndl > 0.4:
        intent = MovementIntent.LIFT_ARM
    elif ntr < 0.2 and ndl < 0.2 and (nsf > 0.3 or nsb > 0.3):
        intent = MovementIntent.LOWER_ARM
    elif nsf > 0.5 and nsb < 0.3:
        intent = MovementIntent.FLEX_ELBOW
    elif nsb > 0.5 and nsf < 0.3:
        intent = MovementIntent.EXTEND_ELBOW
    elif ndl > 0.4 and nsb > 0.4:
        intent = MovementIntent.EXTEND_ARM
    elif ndl > 0.3 and nsf > 0.4:
        intent = MovementIntent.RETRACT_ARM
    elif nsf > 0.6 and ntr > 0.3:
        intent = MovementIntent.GRASP
    elif nsf < 0.15 and nsb < 0.15 and ntr < 0.2:
        intent = MovementIntent.RELEASE

    return {'intent': intent}


def analyze_body_signals(
    signals:    Dict[str, float],
    target_arm: TargetArm,
) -> Dict[str, Any]:
    if target_arm == TargetArm.NONE:
        return {'strength': 0.0, 'speed_modifier': 0.0}

    if target_arm == TargetArm.RIGHT:
        chest = signals.get('chest_right', 0)
    elif target_arm == TargetArm.LEFT:
        chest = signals.get('chest_left', 0)
    else:
        chest = max(
            signals.get('chest_right', 0),
            signals.get('chest_left',  0),
        )

    nc           = normalize_signal(chest)
    strength     = max(0.1, min(1.0, nc))
    speed_modifier = (
        1.5 if nc > 0.7 else
        1.0 if nc > 0.4 else
        0.6
    )

    return {
        'strength':      round(strength, 3),
        'speed_modifier': round(speed_modifier, 2),
    }


def combined_analysis(signals: Dict[str, float]) -> Dict[str, Any]:
    eeg    = analyze_eeg_signals(signals)
    target = eeg['target_arm']

    if target == TargetArm.NONE:
        return {
            'target_arm':    'none',
            'intent':        'idle',
            'strength':      0.0,
            'speed_modifier': 0.0,
            'can_execute':   False,
        }

    phantom     = analyze_phantom_signals(signals, target)
    body        = analyze_body_signals(signals, target)
    can_execute = phantom['intent'] != MovementIntent.IDLE

    return {
        'target_arm':    target.value,
        'intent':        phantom['intent'].value,
        'strength':      body['strength'],
        'speed_modifier': body['speed_modifier'],
        'can_execute':   can_execute,
    }


def _forward_to_verify(
    result:     Dict[str, Any],
    patient_id: str,
    posture:    str,
) -> tuple[str, Optional[dict]]:
    """
    Пересылает результат анализа в neural_verify_upper.
    Использует get_client() — патчится в тестах.
    Возвращает (forwarded_status, verify_result).
    """
    try:
        with get_client() as c:
            resp = c.post(f"{NEURAL_VERIFY_URL}/process", json={
                "target_arm":    result['target_arm'],
                "intent":        result['intent'],
                "strength":      result['strength'],
                "speed_modifier": result['speed_modifier'],
                "can_execute":   result['can_execute'],
                "patient_id":    patient_id,
                "posture":       posture,
            })
            return 'yes', resp.json()
    except Exception as e:
        logger.error(f"Forward to verify failed: {e}")
        return 'error', {"error": str(e)}


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Neural Signal System", version="3.1")


@app.post('/analyze')
def analyze_signals(body: SignalsInput = SignalsInput()):
    """
    Анализирует нейросигналы и определяет intent движения.

    Цепочка при can_execute=True и forward_to_verify=True:
      neural_signal_system → neural_verify_upper → arm_force_limits → arm_movement
    """
    if not body.signals:
        raise HTTPException(400, 'No signals provided')

    result = combined_analysis(body.signals)

    logger.info(
        f"Analysis: arm={result['target_arm']}, "
        f"intent={result['intent']}, strength={result['strength']}"
    )

    # Сохраняем в БД
    session   = SessionLocal()
    forwarded = 'no'
    try:
        session.add(SignalReadingDB(
            eeg_c3=body.signals.get('eeg_c3', 0),
            eeg_c4=body.signals.get('eeg_c4', 0),
            stump_right_front=body.signals.get('stump_right_front', 0),
            stump_right_back=body.signals.get('stump_right_back',   0),
            stump_left_front=body.signals.get('stump_left_front',   0),
            stump_left_back=body.signals.get('stump_left_back',     0),
            shoulder_right_trapezius=body.signals.get(
                'shoulder_right_trapezius', 0
            ),
            shoulder_right_deltoid=body.signals.get(
                'shoulder_right_deltoid', 0
            ),
            shoulder_left_trapezius=body.signals.get(
                'shoulder_left_trapezius', 0
            ),
            shoulder_left_deltoid=body.signals.get(
                'shoulder_left_deltoid', 0
            ),
            chest_right=body.signals.get('chest_right', 0),
            chest_left=body.signals.get('chest_left',   0),
            detected_arm=result['target_arm'],
            detected_intent=result['intent'],
            strength=result['strength'],
        ))
        session.commit()
    finally:
        session.close()

    # Пересылаем в neural_verify_upper
    verify_result = None
    if body.forward_to_verify and result['can_execute']:
        forwarded, verify_result = _forward_to_verify(
            result=result,
            patient_id=body.patient_id,
            posture=body.posture,
        )
        logger.info(
            f"Forwarded to verify: status={forwarded}, "
            f"result={verify_result}"
        )

    result['forwarded']     = forwarded
    result['verify_result'] = verify_result
    return result


@app.get('/readings')
def get_readings(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        readings = (
            session.query(SignalReadingDB)
            .order_by(SignalReadingDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id':        r.id,
            'arm':       r.detected_arm,
            'intent':    r.detected_intent,
            'strength':  r.strength,
            'forwarded': r.forwarded,
        } for r in readings]
    finally:
        session.close()


@app.get('/health')
def health():
    return {'status': 'healthy', 'module': MODULE_NAME}


@app.post('/reset')
def reset():
    return {'ok': True}


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)