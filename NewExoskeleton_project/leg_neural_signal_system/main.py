# leg_neural_signal_system/main.py
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
PORT = int(os.getenv('PORT', 9001))
MODULE_NAME = os.getenv('MODULE_NAME', 'leg_neural_signal_system')

NEURAL_VERIFY_URL = os.getenv(
    "NEURAL_VERIFY_LOWER_URL", "http://localhost:7104"
)
REQUEST_TIMEOUT = 5.0

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///leg_neural_signals.db'
engine       = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()


# ── Перечисления ──────────────────────────────────────────────────────────────

class TargetLeg(str, Enum):
    RIGHT = "right"
    LEFT  = "left"
    BOTH  = "both"
    NONE  = "none"


class MovementIntent(str, Enum):
    FLEX_KNEE    = "flex_knee"
    EXTEND_KNEE  = "extend_knee"
    SQUAT        = "squat"
    STAND_UP     = "stand_up"
    MOVE_FORWARD = "move_forward"
    MOVE_BACKWARD = "move_backward"
    TURN_LEFT    = "turn_left"
    TURN_RIGHT   = "turn_right"
    PIVOT_LEFT   = "pivot_left"
    PIVOT_RIGHT  = "pivot_right"
    STOP         = "stop"
    BRAKE        = "brake"
    SIT_DOWN     = "sit_down"
    IDLE         = "idle"


# ── БД ────────────────────────────────────────────────────────────────────────

class SignalReadingDB(Base):
    __tablename__ = 'signal_readings'
    id              = Column(Integer, primary_key=True, autoincrement=True)
    detected_leg    = Column(String(20))
    detected_intent = Column(String(50))
    strength        = Column(Float)
    forwarded       = Column(String(10), default='no')
    created_at      = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

VALID_SENSOR_NAMES = {
    'eeg_cz', 'eeg_c1', 'eeg_c2',
    'stump_right_quadriceps', 'stump_right_hamstring',
    'stump_left_quadriceps',  'stump_left_hamstring',
    'hip_right_flexor',  'hip_right_extensor',
    'hip_left_flexor',   'hip_left_extensor',
    'glute_right', 'glute_left',
    'abs_upper',   'abs_lower',
}


# ── Pydantic модели ───────────────────────────────────────────────────────────

class SignalsInput(BaseModel):
    signals:           Optional[Dict[str, float]] = None
    patient_id:        str  = ""
    posture:           str  = "standing"
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
    return max(0.0, min(1.0, (value - baseline) / (max_value - baseline)))


def combined_analysis(signals: Dict[str, float]) -> Dict[str, Any]:
    """
    Анализирует нейросигналы нижних конечностей.
    Определяет целевую ногу и интент движения.
    """
    cz = normalize_signal(signals.get('eeg_cz', 0), 0, 100)
    c1 = normalize_signal(signals.get('eeg_c1', 0), 0, 100)
    c2 = normalize_signal(signals.get('eeg_c2', 0), 0, 100)

    threshold = 0.3
    target    = TargetLeg.NONE

    if cz > threshold and c1 > threshold and c2 > threshold:
        target = TargetLeg.BOTH
    elif c1 > threshold and c1 > c2:
        target = TargetLeg.RIGHT
    elif c2 > threshold and c2 > c1:
        target = TargetLeg.LEFT
    elif cz > threshold:
        target = TargetLeg.BOTH

    if target == TargetLeg.NONE:
        return {
            'target_leg':    'none',
            'intent':        'idle',
            'strength':      0.0,
            'speed_modifier': 0.0,
            'can_execute':   False,
        }

    # Мышечные сигналы
    rq  = normalize_signal(signals.get('stump_right_quadriceps', 0))
    rh  = normalize_signal(signals.get('stump_right_hamstring',  0))
    lq  = normalize_signal(signals.get('stump_left_quadriceps',  0))
    lh  = normalize_signal(signals.get('stump_left_hamstring',   0))
    rhf = normalize_signal(signals.get('hip_right_flexor',       0))
    rhe = normalize_signal(signals.get('hip_right_extensor',     0))
    lhf = normalize_signal(signals.get('hip_left_flexor',        0))
    lhe = normalize_signal(signals.get('hip_left_extensor',      0))
    au  = normalize_signal(signals.get('abs_upper',              0))
    al  = normalize_signal(signals.get('abs_lower',              0))

    # Определение интента
    intent = MovementIntent.IDLE
    if au > 0.7 and al > 0.7:
        intent = MovementIntent.BRAKE
    elif au > 0.5 or al > 0.5:
        intent = MovementIntent.STOP
    elif rq > 0.6 and lq > 0.6:
        intent = MovementIntent.STAND_UP
    elif rh > 0.6 and lh > 0.6:
        intent = MovementIntent.SQUAT
    elif rhf > 0.5 and lhf > 0.5:
        intent = MovementIntent.MOVE_FORWARD
    elif rhe > 0.5 and lhe > 0.5:
        intent = MovementIntent.MOVE_BACKWARD
    elif rhf > 0.5 and lhe > 0.3:
        intent = MovementIntent.TURN_LEFT
    elif lhf > 0.5 and rhe > 0.3:
        intent = MovementIntent.TURN_RIGHT

    # Сила и скорость по ягодичным мышцам
    gr     = normalize_signal(signals.get('glute_right', 0))
    gl     = normalize_signal(signals.get('glute_left',  0))
    glute  = (
        (gr + gl) / 2 if target == TargetLeg.BOTH else
        gr if target == TargetLeg.RIGHT else
        gl
    )
    strength      = max(0.1, min(1.0, glute))
    speed_modifier = (
        1.5 if strength > 0.7 else
        1.0 if strength > 0.4 else
        0.6
    )

    can_execute = intent != MovementIntent.IDLE

    return {
        'target_leg':    target.value,
        'intent':        intent.value,
        'strength':      round(strength, 3),
        'speed_modifier': round(speed_modifier, 2),
        'can_execute':   can_execute,
    }


def _forward_to_verify(
    result:     Dict[str, Any],
    patient_id: str,
    posture:    str,
) -> tuple[str, Optional[dict]]:
    """
    Пересылает результат анализа в neural_verify_lower.
    Использует get_client() — патчится в тестах.
    Возвращает (forwarded_status, verify_result).
    """
    try:
        with get_client() as c:
            resp = c.post(f"{NEURAL_VERIFY_URL}/process", json={
                "target_leg":    result['target_leg'],
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

app = FastAPI(title="Leg Neural Signal System", version="3.1")


@app.post('/analyze')
def analyze_signals(body: SignalsInput = SignalsInput()):
    """
    Анализирует нейросигналы нижних конечностей.

    Цепочка при can_execute=True и forward_to_verify=True:
      leg_neural_signal → neural_verify_lower → leg_force_limits → leg_movement
    """
    if not body.signals:
        raise HTTPException(400, 'No signals provided')

    result = combined_analysis(body.signals)

    logger.info(
        f"Analysis: leg={result['target_leg']}, "
        f"intent={result['intent']}, strength={result['strength']}"
    )

    # Сохраняем в БД
    session   = SessionLocal()
    forwarded = 'no'
    try:
        session.add(SignalReadingDB(
            detected_leg=result['target_leg'],
            detected_intent=result['intent'],
            strength=result['strength'],
        ))
        session.commit()
    finally:
        session.close()

    # Пересылаем в neural_verify_lower
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


@app.get('/health')
def health():
    return {'status': 'healthy', 'module': MODULE_NAME}


@app.post('/reset')
def reset():
    return {'ok': True}


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)