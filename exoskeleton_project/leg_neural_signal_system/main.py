import os
import logging
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = 9001
MODULE_NAME = os.getenv('MODULE_NAME', 'leg_neural_signal_system')
SIGNALS_FILE_PATH = os.getenv('SIGNALS_FILE', 'leg_signals.txt')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///leg_neural_signals.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class TargetLeg(str, Enum):
    RIGHT = "right"
    LEFT = "left"
    BOTH = "both"
    NONE = "none"


class MovementIntent(str, Enum):
    FLEX_KNEE = "flex_knee"
    EXTEND_KNEE = "extend_knee"
    SQUAT = "squat"
    STAND_UP = "stand_up"
    MOVE_FORWARD = "move_forward"
    MOVE_BACKWARD = "move_backward"
    TURN_LEFT = "turn_left"
    TURN_RIGHT = "turn_right"
    PIVOT_LEFT = "pivot_left"
    PIVOT_RIGHT = "pivot_right"
    STOP = "stop"
    BRAKE = "brake"
    SIT_DOWN = "sit_down"
    IDLE = "idle"


class SignalReadingDB(Base):
    __tablename__ = 'signal_readings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    eeg_cz = Column(Float, default=0.0)
    eeg_c1 = Column(Float, default=0.0)
    eeg_c2 = Column(Float, default=0.0)
    stump_right_quadriceps = Column(Float, default=0.0)
    stump_right_hamstring = Column(Float, default=0.0)
    stump_left_quadriceps = Column(Float, default=0.0)
    stump_left_hamstring = Column(Float, default=0.0)
    hip_right_flexor = Column(Float, default=0.0)
    hip_right_extensor = Column(Float, default=0.0)
    hip_left_flexor = Column(Float, default=0.0)
    hip_left_extensor = Column(Float, default=0.0)
    glute_right = Column(Float, default=0.0)
    glute_left = Column(Float, default=0.0)
    abs_upper = Column(Float, default=0.0)
    abs_lower = Column(Float, default=0.0)
    detected_leg = Column(String(20))
    detected_intent = Column(String(50))
    strength = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'signals': {
                'eeg_cz': self.eeg_cz, 'eeg_c1': self.eeg_c1,
                'eeg_c2': self.eeg_c2,
                'stump_right_quadriceps': self.stump_right_quadriceps,
                'stump_right_hamstring': self.stump_right_hamstring,
                'stump_left_quadriceps': self.stump_left_quadriceps,
                'stump_left_hamstring': self.stump_left_hamstring,
                'hip_right_flexor': self.hip_right_flexor,
                'hip_right_extensor': self.hip_right_extensor,
                'hip_left_flexor': self.hip_left_flexor,
                'hip_left_extensor': self.hip_left_extensor,
                'glute_right': self.glute_right,
                'glute_left': self.glute_left,
                'abs_upper': self.abs_upper,
                'abs_lower': self.abs_lower
            },
            'result': {
                'leg': self.detected_leg,
                'intent': self.detected_intent,
                'strength': self.strength
            },
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if self.created_at else None
        }


class MovementLogDB(Base):
    __tablename__ = 'movement_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    leg = Column(String(20))
    intent = Column(String(50))
    strength = Column(Float)
    speed_modifier = Column(Float)
    source = Column(String(50), default='leg_neural_analysis')
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

VALID_SENSOR_NAMES = {
    'eeg_cz', 'eeg_c1', 'eeg_c2',
    'stump_right_quadriceps', 'stump_right_hamstring',
    'stump_left_quadriceps', 'stump_left_hamstring',
    'hip_right_flexor', 'hip_right_extensor',
    'hip_left_flexor', 'hip_left_extensor',
    'glute_right', 'glute_left',
    'abs_upper', 'abs_lower'
}


class SignalsInput(BaseModel):
    signals: Optional[Dict[str, float]] = None


def parse_signals_line(line: str) -> Optional[Dict[str, float]]:
    signals = {name: 0.0 for name in VALID_SENSOR_NAMES}
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    parts = line.split()
    found_valid = False
    for part in parts:
        if ':' not in part:
            continue
        name, value = part.split(':', 1)
        name = name.strip().lower()
        if name in VALID_SENSOR_NAMES:
            try:
                signals[name] = float(value.strip())
                found_valid = True
            except ValueError:
                logger.warning(f"Invalid value for {name}: {value}")
    return signals if found_valid else None


def read_signal_from_file(file_path: str) -> Optional[Dict[str, float]]:
    if not os.path.exists(file_path):
        logger.error(f"Signals file not found: {file_path}")
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            line = f.readline()
            return parse_signals_line(line)
    except Exception as e:
        logger.error(f"Error reading signals file: {e}")
        return None


def normalize_signal(
    value: float, baseline: float = 10.0, max_value: float = 150.0
) -> float:
    if max_value <= baseline:
        return 0.0
    normalized = (value - baseline) / (max_value - baseline)
    return max(0.0, min(1.0, normalized))


def analyze_eeg_signals(signals: Dict[str, float]) -> Dict[str, Any]:
    cz = normalize_signal(signals.get('eeg_cz', 0), 0, 100)
    c1 = normalize_signal(signals.get('eeg_c1', 0), 0, 100)
    c2 = normalize_signal(signals.get('eeg_c2', 0), 0, 100)

    threshold = 0.3
    target_leg = TargetLeg.NONE

    if cz > threshold and c1 > threshold and c2 > threshold:
        target_leg = TargetLeg.BOTH
    elif c1 > threshold and c1 > c2:
        target_leg = TargetLeg.RIGHT
    elif c2 > threshold and c2 > c1:
        target_leg = TargetLeg.LEFT
    elif cz > threshold:
        target_leg = TargetLeg.BOTH

    return {
        'target_leg': target_leg,
        'cz_activation': round(cz, 3),
        'c1_activation': round(c1, 3),
        'c2_activation': round(c2, 3)
    }


def analyze_movement_signals(
    signals: Dict[str, float], target_leg: TargetLeg
) -> Dict[str, Any]:
    if target_leg == TargetLeg.NONE:
        return {'intent': MovementIntent.IDLE}

    rq = normalize_signal(signals.get('stump_right_quadriceps', 0))
    rh = normalize_signal(signals.get('stump_right_hamstring', 0))
    lq = normalize_signal(signals.get('stump_left_quadriceps', 0))
    lh = normalize_signal(signals.get('stump_left_hamstring', 0))
    rhf = normalize_signal(signals.get('hip_right_flexor', 0))
    rhe = normalize_signal(signals.get('hip_right_extensor', 0))
    lhf = normalize_signal(signals.get('hip_left_flexor', 0))
    lhe = normalize_signal(signals.get('hip_left_extensor', 0))
    au = normalize_signal(signals.get('abs_upper', 0))
    al = normalize_signal(signals.get('abs_lower', 0))

    intent = MovementIntent.IDLE

    if au > 0.7 and al > 0.7:
        intent = MovementIntent.BRAKE
    elif au > 0.5 or al > 0.5:
        intent = MovementIntent.STOP
    elif rq > 0.6 and lq > 0.6:
        intent = MovementIntent.STAND_UP
    elif rh > 0.6 and lh > 0.6:
        intent = MovementIntent.SQUAT
    elif target_leg == TargetLeg.RIGHT and rq > 0.5:
        intent = MovementIntent.EXTEND_KNEE
    elif target_leg == TargetLeg.RIGHT and rh > 0.5:
        intent = MovementIntent.FLEX_KNEE
    elif target_leg == TargetLeg.LEFT and lq > 0.5:
        intent = MovementIntent.EXTEND_KNEE
    elif target_leg == TargetLeg.LEFT and lh > 0.5:
        intent = MovementIntent.FLEX_KNEE
    elif rhf > 0.5 and lhf > 0.5:
        intent = MovementIntent.MOVE_FORWARD
    elif rhe > 0.5 and lhe > 0.5:
        intent = MovementIntent.MOVE_BACKWARD
    elif rhf > 0.5 and lhe > 0.3:
        intent = MovementIntent.TURN_LEFT
    elif lhf > 0.5 and rhe > 0.3:
        intent = MovementIntent.TURN_RIGHT
    elif rhf > 0.6 and lhe > 0.6:
        intent = MovementIntent.PIVOT_LEFT
    elif lhf > 0.6 and rhe > 0.6:
        intent = MovementIntent.PIVOT_RIGHT
    elif rh > 0.4 and lh > 0.4 and rhe > 0.3:
        intent = MovementIntent.SIT_DOWN

    return {
        'intent': intent,
        'right_quadriceps': round(rq, 3),
        'right_hamstring': round(rh, 3),
        'left_quadriceps': round(lq, 3),
        'left_hamstring': round(lh, 3),
        'right_hip_flexor': round(rhf, 3),
        'right_hip_extensor': round(rhe, 3),
        'left_hip_flexor': round(lhf, 3),
        'left_hip_extensor': round(lhe, 3),
        'abs_activation': round((au + al) / 2, 3)
    }


def analyze_strength_signals(
    signals: Dict[str, float], target_leg: TargetLeg
) -> Dict[str, Any]:
    if target_leg == TargetLeg.NONE:
        return {'strength': 0.0, 'speed_modifier': 0.0}

    gr = normalize_signal(signals.get('glute_right', 0))
    gl = normalize_signal(signals.get('glute_left', 0))

    if target_leg == TargetLeg.RIGHT:
        glute = gr
    elif target_leg == TargetLeg.LEFT:
        glute = gl
    else:
        glute = (gr + gl) / 2

    strength = max(0.1, min(1.0, glute))
    if strength > 0.7:
        speed_modifier = 1.5
    elif strength > 0.4:
        speed_modifier = 1.0
    else:
        speed_modifier = 0.6

    return {
        'strength': round(strength, 3),
        'speed_modifier': round(speed_modifier, 2),
        'glute_right': round(gr, 3),
        'glute_left': round(gl, 3)
    }


def combined_analysis(signals: Dict[str, float]) -> Dict[str, Any]:
    eeg_result = analyze_eeg_signals(signals)
    target_leg = eeg_result['target_leg']

    if target_leg == TargetLeg.NONE:
        return {
            'target_leg': 'none', 'intent': 'idle',
            'strength': 0.0, 'speed_modifier': 0.0,
            'can_execute': False,
            'analysis': {
                'level_1_eeg': eeg_result,
                'level_2_movement': None,
                'level_3_strength': None
            }
        }

    movement_result = analyze_movement_signals(signals, target_leg)
    strength_result = analyze_strength_signals(signals, target_leg)
    can_execute = movement_result['intent'] != MovementIntent.IDLE

    return {
        'target_leg': target_leg.value,
        'intent': movement_result['intent'].value,
        'strength': strength_result['strength'],
        'speed_modifier': strength_result['speed_modifier'],
        'can_execute': can_execute,
        'analysis': {
            'level_1_eeg': {
                'target_leg': target_leg.value,
                'cz_activation': eeg_result['cz_activation'],
                'c1_activation': eeg_result['c1_activation'],
                'c2_activation': eeg_result['c2_activation']
            },
            'level_2_movement': movement_result,
            'level_3_strength': strength_result
        }
    }


app = FastAPI(title="Leg Neural Signal System", version="2.0")


@app.post('/analyze')
def analyze_signals(body: SignalsInput = SignalsInput()):
    if body.signals:
        signals = body.signals
        logger.info("Analyzing leg signals from request body")
    else:
        signals = read_signal_from_file(SIGNALS_FILE_PATH)
        logger.info(f"Analyzing leg signals from file: {SIGNALS_FILE_PATH}")

    if signals is None:
        raise HTTPException(status_code=400, detail='No signals available')

    result = combined_analysis(signals)
    logger.info(
        f"Leg analysis: leg={result['target_leg']}, "
        f"intent={result['intent']}, strength={result['strength']}"
    )

    session = SessionLocal()
    try:
        reading = SignalReadingDB(
            eeg_cz=signals.get('eeg_cz', 0),
            eeg_c1=signals.get('eeg_c1', 0),
            eeg_c2=signals.get('eeg_c2', 0),
            stump_right_quadriceps=signals.get('stump_right_quadriceps', 0),
            stump_right_hamstring=signals.get('stump_right_hamstring', 0),
            stump_left_quadriceps=signals.get('stump_left_quadriceps', 0),
            stump_left_hamstring=signals.get('stump_left_hamstring', 0),
            hip_right_flexor=signals.get('hip_right_flexor', 0),
            hip_right_extensor=signals.get('hip_right_extensor', 0),
            hip_left_flexor=signals.get('hip_left_flexor', 0),
            hip_left_extensor=signals.get('hip_left_extensor', 0),
            glute_right=signals.get('glute_right', 0),
            glute_left=signals.get('glute_left', 0),
            abs_upper=signals.get('abs_upper', 0),
            abs_lower=signals.get('abs_lower', 0),
            detected_leg=result['target_leg'],
            detected_intent=result['intent'],
            strength=result['strength']
        )
        session.add(reading)

        if result['can_execute']:
            log = MovementLogDB(
                leg=result['target_leg'],
                intent=result['intent'],
                strength=result['strength'],
                speed_modifier=result['speed_modifier']
            )
            session.add(log)

        session.commit()
    finally:
        session.close()

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
        return [r.to_dict() for r in readings]
    finally:
        session.close()


@app.get('/readings/{reading_id}')
def get_reading(reading_id: int):
    session = SessionLocal()
    try:
        reading = session.query(SignalReadingDB).get(reading_id)
        if not reading:
            raise HTTPException(status_code=404, detail='Not found')
        return reading.to_dict()
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
            'id': l.id, 'leg': l.leg, 'intent': l.intent,
            'strength': l.strength, 'speed_modifier': l.speed_modifier,
            'source': l.source,
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