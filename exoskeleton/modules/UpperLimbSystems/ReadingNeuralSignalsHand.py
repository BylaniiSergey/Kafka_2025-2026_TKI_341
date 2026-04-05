import os
from datetime import datetime
from enum import Enum
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from werkzeug.exceptions import HTTPException
import threading
from typing import Optional, Dict, Any
import logging

HOST = '0.0.0.0'
PORT = 8001
MODULE_NAME = os.getenv('MODULE_NAME', 'neural_signal_system')
SIGNALS_FILE_PATH = os.getenv('SIGNALS_FILE', 'signals.txt')

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///neural_signals.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TargetArm(Enum):
    RIGHT = "right"
    LEFT = "left"
    BOTH = "both"
    NONE = "none"


class MovementIntent(Enum):
    LIFT_ARM = "lift_arm"
    LOWER_ARM = "lower_arm"
    EXTEND_ARM = "extend_arm"
    RETRACT_ARM = "retract_arm"
    FLEX_ELBOW = "flex_elbow"
    EXTEND_ELBOW = "extend_elbow"
    GRASP = "grasp"
    RELEASE = "release"
    IDLE = "idle"


class SignalReading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    eeg_c3 = db.Column(db.Float, default=0.0)
    eeg_c4 = db.Column(db.Float, default=0.0)
    
    stump_right_front = db.Column(db.Float, default=0.0)
    stump_right_back = db.Column(db.Float, default=0.0)
    stump_left_front = db.Column(db.Float, default=0.0)
    stump_left_back = db.Column(db.Float, default=0.0)
    
    shoulder_right_trapezius = db.Column(db.Float, default=0.0)
    shoulder_right_deltoid = db.Column(db.Float, default=0.0)
    shoulder_left_trapezius = db.Column(db.Float, default=0.0)
    shoulder_left_deltoid = db.Column(db.Float, default=0.0)
    
    chest_right = db.Column(db.Float, default=0.0)
    chest_left = db.Column(db.Float, default=0.0)
    
    detected_arm = db.Column(db.String(20))
    detected_intent = db.Column(db.String(50))
    strength = db.Column(db.Float)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'signals': {
                'eeg_c3': self.eeg_c3,
                'eeg_c4': self.eeg_c4,
                'stump_right_front': self.stump_right_front,
                'stump_right_back': self.stump_right_back,
                'stump_left_front': self.stump_left_front,
                'stump_left_back': self.stump_left_back,
                'shoulder_right_trapezius': self.shoulder_right_trapezius,
                'shoulder_right_deltoid': self.shoulder_right_deltoid,
                'shoulder_left_trapezius': self.shoulder_left_trapezius,
                'shoulder_left_deltoid': self.shoulder_left_deltoid,
                'chest_right': self.chest_right,
                'chest_left': self.chest_left
            },
            'result': {
                'arm': self.detected_arm,
                'intent': self.detected_intent,
                'strength': self.strength
            },
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S')
        }


VALID_SENSOR_NAMES = {
    'eeg_c3', 'eeg_c4',
    'stump_right_front', 'stump_right_back',
    'stump_left_front', 'stump_left_back',
    'shoulder_right_trapezius', 'shoulder_right_deltoid',
    'shoulder_left_trapezius', 'shoulder_left_deltoid',
    'chest_right', 'chest_left'
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
    c3_value = signals.get('eeg_c3', 0)
    c4_value = signals.get('eeg_c4', 0)
    
    c3_activation = normalize_signal(c3_value, baseline=0, max_value=100)
    c4_activation = normalize_signal(c4_value, baseline=0, max_value=100)
    
    threshold = 0.3
    target_arm = TargetArm.NONE
    
    if c3_activation > threshold and c4_activation > threshold:
        target_arm = TargetArm.BOTH
    elif c3_activation > threshold and c3_activation > c4_activation:
        target_arm = TargetArm.RIGHT
    elif c4_activation > threshold and c4_activation > c3_activation:
        target_arm = TargetArm.LEFT
    
    return {
        'target_arm': target_arm,
        'c3_activation': round(c3_activation, 3),
        'c4_activation': round(c4_activation, 3)
    }


def analyze_phantom_signals(signals: Dict[str, float], target_arm: TargetArm) -> Dict[str, Any]:
    if target_arm == TargetArm.NONE:
        return {'intent': MovementIntent.IDLE}
    
    if target_arm == TargetArm.RIGHT:
        stump_front = signals.get('stump_right_front', 0)
        stump_back = signals.get('stump_right_back', 0)
        trapezius = signals.get('shoulder_right_trapezius', 0)
        deltoid = signals.get('shoulder_right_deltoid', 0)
    elif target_arm == TargetArm.LEFT:
        stump_front = signals.get('stump_left_front', 0)
        stump_back = signals.get('stump_left_back', 0)
        trapezius = signals.get('shoulder_left_trapezius', 0)
        deltoid = signals.get('shoulder_left_deltoid', 0)
    else:
        stump_front = max(signals.get('stump_right_front', 0), signals.get('stump_left_front', 0))
        stump_back = max(signals.get('stump_right_back', 0), signals.get('stump_left_back', 0))
        trapezius = max(signals.get('shoulder_right_trapezius', 0), signals.get('shoulder_left_trapezius', 0))
        deltoid = max(signals.get('shoulder_right_deltoid', 0), signals.get('shoulder_left_deltoid', 0))
    
    norm_stump_front = normalize_signal(stump_front)
    norm_stump_back = normalize_signal(stump_back)
    norm_trapezius = normalize_signal(trapezius)
    norm_deltoid = normalize_signal(deltoid)
    
    intent = MovementIntent.IDLE
    
    # Движения плеча
    if norm_trapezius > 0.5 and norm_deltoid > 0.4:
        intent = MovementIntent.LIFT_ARM
    elif norm_trapezius < 0.2 and norm_deltoid < 0.2 and (norm_stump_front > 0.3 or norm_stump_back > 0.3):
        intent = MovementIntent.LOWER_ARM
    # Движения локтя
    elif norm_stump_front > 0.5 and norm_stump_back < 0.3:
        intent = MovementIntent.FLEX_ELBOW
    elif norm_stump_back > 0.5 and norm_stump_front < 0.3:
        intent = MovementIntent.EXTEND_ELBOW
    # Движения руки
    elif norm_deltoid > 0.4 and norm_stump_back > 0.4:
        intent = MovementIntent.EXTEND_ARM
    elif norm_deltoid > 0.3 and norm_stump_front > 0.4:
        intent = MovementIntent.RETRACT_ARM
    # Захват
    elif norm_stump_front > 0.6 and norm_trapezius > 0.3:
        intent = MovementIntent.GRASP
    elif norm_stump_front < 0.15 and norm_stump_back < 0.15 and norm_trapezius < 0.2:
        intent = MovementIntent.RELEASE
    
    return {
        'intent': intent,
        'stump_front': round(norm_stump_front, 3),
        'stump_back': round(norm_stump_back, 3),
        'trapezius': round(norm_trapezius, 3),
        'deltoid': round(norm_deltoid, 3)
    }


def analyze_body_signals(signals: Dict[str, float], target_arm: TargetArm) -> Dict[str, Any]:
    if target_arm == TargetArm.NONE:
        return {'strength': 0.0, 'speed_modifier': 0.0}
    
    if target_arm == TargetArm.RIGHT:
        chest = signals.get('chest_right', 0)
    elif target_arm == TargetArm.LEFT:
        chest = signals.get('chest_left', 0)
    else:
        chest = max(signals.get('chest_right', 0), signals.get('chest_left', 0))
    
    norm_chest = normalize_signal(chest)
    strength = max(0.1, min(1.0, norm_chest))
    
    if norm_chest > 0.7:
        speed_modifier = 1.5
    elif norm_chest > 0.4:
        speed_modifier = 1.0
    else:
        speed_modifier = 0.6
    
    return {
        'strength': round(strength, 3),
        'speed_modifier': round(speed_modifier, 2),
        'chest_activation': round(norm_chest, 3)
    }


def combined_analysis(signals: Dict[str, float]) -> Dict[str, Any]:
    eeg_result = analyze_eeg_signals(signals)
    target_arm = eeg_result['target_arm']
    
    if target_arm == TargetArm.NONE:
        return {
            'target_arm': 'none',
            'intent': 'idle',
            'strength': 0.0,
            'speed_modifier': 0.0,
            'can_execute': False,
            'analysis': {
                'level_1_eeg': {
                    'target_arm': 'none',
                    'c3_activation': eeg_result['c3_activation'],
                    'c4_activation': eeg_result['c4_activation']
                },
                'level_2_phantom': None,
                'level_3_body': None
            }
        }
    
    phantom_result = analyze_phantom_signals(signals, target_arm)
    body_result = analyze_body_signals(signals, target_arm)
    can_execute = phantom_result['intent'] != MovementIntent.IDLE
    
    return {
        'target_arm': target_arm.value,
        'intent': phantom_result['intent'].value,
        'strength': body_result['strength'],
        'speed_modifier': body_result['speed_modifier'],
        'can_execute': can_execute,
        'analysis': {
            'level_1_eeg': {
                'target_arm': target_arm.value,
                'c3_activation': eeg_result['c3_activation'],
                'c4_activation': eeg_result['c4_activation']
            },
            'level_2_phantom': {
                'intent': phantom_result['intent'].value,
                'stump_front': phantom_result.get('stump_front'),
                'stump_back': phantom_result.get('stump_back'),
                'trapezius': phantom_result.get('trapezius'),
                'deltoid': phantom_result.get('deltoid')
            },
            'level_3_body': body_result
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
        eeg_c3=signals.get('eeg_c3', 0),
        eeg_c4=signals.get('eeg_c4', 0),
        stump_right_front=signals.get('stump_right_front', 0),
        stump_right_back=signals.get('stump_right_back', 0),
        stump_left_front=signals.get('stump_left_front', 0),
        stump_left_back=signals.get('stump_left_back', 0),
        shoulder_right_trapezius=signals.get('shoulder_right_trapezius', 0),
        shoulder_right_deltoid=signals.get('shoulder_right_deltoid', 0),
        shoulder_left_trapezius=signals.get('shoulder_left_trapezius', 0),
        shoulder_left_deltoid=signals.get('shoulder_left_deltoid', 0),
        chest_right=signals.get('chest_right', 0),
        chest_left=signals.get('chest_left', 0),
        detected_arm=result['target_arm'],
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


def start_web():
    threading.Thread(target=lambda: app.run(
        host=HOST, port=PORT, debug=False, use_reloader=False
    )).start()


if __name__ == '__main__':
    app.run(host=HOST, port=PORT, debug=False)