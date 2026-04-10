import os
from datetime import datetime
from enum import Enum
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from werkzeug.exceptions import HTTPException
from typing import Optional, Dict, Any
import logging

HOST = '0.0.0.0'
PORT = 9001
MODULE_NAME = os.getenv('MODULE_NAME', 'leg_neural_signal_system')
SIGNALS_FILE_PATH = os.getenv('SIGNALS_FILE', 'leg_signals.txt')

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///leg_neural_signals.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TargetLeg(Enum):
    RIGHT = "right"
    LEFT = "left"
    BOTH = "both"
    NONE = "none"


class MovementIntent(Enum):
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


class SignalReading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    eeg_cz = db.Column(db.Float, default=0.0)
    eeg_c1 = db.Column(db.Float, default=0.0)
    eeg_c2 = db.Column(db.Float, default=0.0)
    
    stump_right_quadriceps = db.Column(db.Float, default=0.0)
    stump_right_hamstring = db.Column(db.Float, default=0.0)
    stump_left_quadriceps = db.Column(db.Float, default=0.0)
    stump_left_hamstring = db.Column(db.Float, default=0.0)
    
    hip_right_flexor = db.Column(db.Float, default=0.0)
    hip_right_extensor = db.Column(db.Float, default=0.0)
    hip_left_flexor = db.Column(db.Float, default=0.0)
    hip_left_extensor = db.Column(db.Float, default=0.0)
    
    glute_right = db.Column(db.Float, default=0.0)
    glute_left = db.Column(db.Float, default=0.0)
    
    abs_upper = db.Column(db.Float, default=0.0)
    abs_lower = db.Column(db.Float, default=0.0)
    
    detected_leg = db.Column(db.String(20))
    detected_intent = db.Column(db.String(50))
    strength = db.Column(db.Float)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'signals': {
                'eeg_cz': self.eeg_cz,
                'eeg_c1': self.eeg_c1,
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
        }


VALID_SENSOR_NAMES = {
    'eeg_cz', 'eeg_c1', 'eeg_c2',
    'stump_right_quadriceps', 'stump_right_hamstring',
    'stump_left_quadriceps', 'stump_left_hamstring',
    'hip_right_flexor', 'hip_right_extensor',
    'hip_left_flexor', 'hip_left_extensor',
    'glute_right', 'glute_left',
    'abs_upper', 'abs_lower'
}


with app.app_context():
    db.create_all()


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


def normalize_signal(value: float, baseline: float = 10.0, max_value: float = 150.0) -> float:
    if max_value <= baseline:
        return 0.0
    normalized = (value - baseline) / (max_value - baseline)
    return max(0.0, min(1.0, normalized))


def analyze_eeg_signals(signals: Dict[str, float]) -> Dict[str, Any]:
    cz_value = signals.get('eeg_cz', 0)
    c1_value = signals.get('eeg_c1', 0)
    c2_value = signals.get('eeg_c2', 0)
    
    cz_activation = normalize_signal(cz_value, baseline=0, max_value=100)
    c1_activation = normalize_signal(c1_value, baseline=0, max_value=100)
    c2_activation = normalize_signal(c2_value, baseline=0, max_value=100)
    
    threshold = 0.3
    target_leg = TargetLeg.NONE
    
    if cz_activation > threshold and c1_activation > threshold and c2_activation > threshold:
        target_leg = TargetLeg.BOTH
    elif c1_activation > threshold and c1_activation > c2_activation:
        target_leg = TargetLeg.RIGHT
    elif c2_activation > threshold and c2_activation > c1_activation:
        target_leg = TargetLeg.LEFT
    elif cz_activation > threshold:
        target_leg = TargetLeg.BOTH
    
    return {
        'target_leg': target_leg,
        'cz_activation': round(cz_activation, 3),
        'c1_activation': round(c1_activation, 3),
        'c2_activation': round(c2_activation, 3)
    }


def analyze_movement_signals(signals: Dict[str, float], target_leg: TargetLeg) -> Dict[str, Any]:
    if target_leg == TargetLeg.NONE:
        return {'intent': MovementIntent.IDLE}
    
    right_quad = normalize_signal(signals.get('stump_right_quadriceps', 0))
    right_ham = normalize_signal(signals.get('stump_right_hamstring', 0))
    left_quad = normalize_signal(signals.get('stump_left_quadriceps', 0))
    left_ham = normalize_signal(signals.get('stump_left_hamstring', 0))
    
    right_hip_flex = normalize_signal(signals.get('hip_right_flexor', 0))
    right_hip_ext = normalize_signal(signals.get('hip_right_extensor', 0))
    left_hip_flex = normalize_signal(signals.get('hip_left_flexor', 0))
    left_hip_ext = normalize_signal(signals.get('hip_left_extensor', 0))
    
    abs_upper = normalize_signal(signals.get('abs_upper', 0))
    abs_lower = normalize_signal(signals.get('abs_lower', 0))
    
    intent = MovementIntent.IDLE
    
    if abs_upper > 0.7 and abs_lower > 0.7:
        intent = MovementIntent.BRAKE
    elif abs_upper > 0.5 or abs_lower > 0.5:
        intent = MovementIntent.STOP
    elif right_quad > 0.6 and left_quad > 0.6:
        intent = MovementIntent.STAND_UP
    elif right_ham > 0.6 and left_ham > 0.6:
        intent = MovementIntent.SQUAT
    elif target_leg == TargetLeg.RIGHT and right_quad > 0.5:
        intent = MovementIntent.EXTEND_KNEE
    elif target_leg == TargetLeg.RIGHT and right_ham > 0.5:
        intent = MovementIntent.FLEX_KNEE
    elif target_leg == TargetLeg.LEFT and left_quad > 0.5:
        intent = MovementIntent.EXTEND_KNEE
    elif target_leg == TargetLeg.LEFT and left_ham > 0.5:
        intent = MovementIntent.FLEX_KNEE
    elif right_hip_flex > 0.5 and left_hip_flex > 0.5:
        intent = MovementIntent.MOVE_FORWARD
    elif right_hip_ext > 0.5 and left_hip_ext > 0.5:
        intent = MovementIntent.MOVE_BACKWARD
    elif right_hip_flex > 0.5 and left_hip_ext > 0.3:
        intent = MovementIntent.TURN_LEFT
    elif left_hip_flex > 0.5 and right_hip_ext > 0.3:
        intent = MovementIntent.TURN_RIGHT
    elif right_hip_flex > 0.6 and left_hip_ext > 0.6:
        intent = MovementIntent.PIVOT_LEFT
    elif left_hip_flex > 0.6 and right_hip_ext > 0.6:
        intent = MovementIntent.PIVOT_RIGHT
    elif right_ham > 0.4 and left_ham > 0.4 and right_hip_ext > 0.3:
        intent = MovementIntent.SIT_DOWN
    
    return {
        'intent': intent,
        'right_quadriceps': round(right_quad, 3),
        'right_hamstring': round(right_ham, 3),
        'left_quadriceps': round(left_quad, 3),
        'left_hamstring': round(left_ham, 3),
        'right_hip_flexor': round(right_hip_flex, 3),
        'right_hip_extensor': round(right_hip_ext, 3),
        'left_hip_flexor': round(left_hip_flex, 3),
        'left_hip_extensor': round(left_hip_ext, 3),
        'abs_activation': round((abs_upper + abs_lower) / 2, 3)
    }


def analyze_strength_signals(signals: Dict[str, float], target_leg: TargetLeg) -> Dict[str, Any]:
    if target_leg == TargetLeg.NONE:
        return {'strength': 0.0, 'speed_modifier': 0.0}
    
    glute_right = normalize_signal(signals.get('glute_right', 0))
    glute_left = normalize_signal(signals.get('glute_left', 0))
    
    if target_leg == TargetLeg.RIGHT:
        glute = glute_right
    elif target_leg == TargetLeg.LEFT:
        glute = glute_left
    else:
        glute = (glute_right + glute_left) / 2
    
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
        'glute_right': round(glute_right, 3),
        'glute_left': round(glute_left, 3)
    }


def combined_analysis(signals: Dict[str, float]) -> Dict[str, Any]:
    eeg_result = analyze_eeg_signals(signals)
    target_leg = eeg_result['target_leg']
    
    if target_leg == TargetLeg.NONE:
        return {
            'target_leg': 'none',
            'intent': 'idle',
            'strength': 0.0,
            'speed_modifier': 0.0,
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


@app.route('/analyze', methods=['POST'])
def analyze_signals():
    if request.json and 'signals' in request.json:
        signals = request.json['signals']
    else:
        signals = read_signal_from_file(SIGNALS_FILE_PATH)
    
    if signals is None:
        return jsonify({'error': 'No signals available'}), 400
    
    result = combined_analysis(signals)
    
    reading = SignalReading(
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
    db.session.add(reading)
    db.session.commit()
    
    return jsonify(result)


@app.route('/readings', methods=['GET'])
def get_readings():
    limit = request.args.get('limit', 100, type=int)
    readings = SignalReading.query.order_by(SignalReading.created_at.desc()).limit(limit).all()
    return jsonify([r.to_dict() for r in readings])


@app.route('/readings/<int:reading_id>', methods=['GET'])
def get_reading(reading_id):
    reading = SignalReading.query.get_or_404(reading_id)
    return jsonify(reading.to_dict())


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'module': MODULE_NAME})


@app.errorhandler(HTTPException)
def handle_exception(e):
    return jsonify({
        "status": e.code,
        "name": e.name,
    }), e.code


if __name__ == '__main__':
    app.run(host=HOST, port=PORT, debug=False)