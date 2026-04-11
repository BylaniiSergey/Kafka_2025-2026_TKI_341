import os
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
import threading
from werkzeug.exceptions import HTTPException
from datetime import datetime
from enum import Enum

HOST = '0.0.0.0'
PORT = 8006
MODULE_NAME = os.getenv('MODULE_NAME', 'force_control_system')
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///force_control.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


class ForceStatus(Enum):
    IDLE = "idle"
    APPLYING = "applying"
    HOLDING = "holding"
    RELEASING = "releasing"
    OVERLOAD = "overload"
    EMERGENCY_STOP = "emergency_stop"


class ForceReading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    arm = db.Column(db.String(10))
    force_value = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


SAFETY_THRESHOLDS = {
    'max_safe_force': 150.0,
    'emergency_threshold': 200.0
}


force_state = {
    'left': {
        'status': ForceStatus.IDLE,
        'current_force': 0.0,
        'emergency_stop': False
    },
    'right': {
        'status': ForceStatus.IDLE,
        'current_force': 0.0,
        'emergency_stop': False
    }
}


with app.app_context():
    db.create_all()


@app.route('/apply_force', methods=['POST'])
def apply_force():
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    arm = data.get('arm')
    grip_type = data.get('grip_type', 'power')
    target_force = data.get('target_force', 50)
    max_force = data.get('max_force', SAFETY_THRESHOLDS['max_safe_force'])
    
    if arm not in force_state:
        return jsonify({'error': 'Invalid arm specified'}), 400
    
    if force_state[arm]['emergency_stop']:
        return jsonify({'error': 'Emergency stop is active'}), 403
    
    state = force_state[arm]
    state['status'] = ForceStatus.APPLYING
    
    # Ограничение силы
    safe_force = min(target_force, max_force, SAFETY_THRESHOLDS['max_safe_force'])
    
    # Симуляция обнаружения объекта
    object_detected = safe_force > 5
    
    # Применение силы
    applied_force = safe_force
    state['current_force'] = applied_force
    state['status'] = ForceStatus.HOLDING if object_detected else ForceStatus.IDLE
    
    # Проверка на перегрузку
    if applied_force > SAFETY_THRESHOLDS['emergency_threshold']:
        state['status'] = ForceStatus.OVERLOAD
        applied_force = SAFETY_THRESHOLDS['max_safe_force']
        state['current_force'] = applied_force
    
    # Логирование показаний
    reading = ForceReading(arm=arm, force_value=applied_force)
    db.session.add(reading)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'arm': arm,
        'target_force': target_force,
        'applied_force': round(applied_force, 2),
        'object_detected': object_detected,
        'status': state['status'].value
    })


@app.route('/release', methods=['POST'])
def release():
    data = request.json or {}
    arm = data.get('arm')
    
    if arm and arm not in force_state:
        return jsonify({'error': 'Invalid arm'}), 400
    
    arms_to_release = [arm] if arm else ['left', 'right']
    
    for current_arm in arms_to_release:
        state = force_state[current_arm]
        state['status'] = ForceStatus.RELEASING
        state['current_force'] = 0.0
        state['status'] = ForceStatus.IDLE
    
    return jsonify({
        'success': True,
        'arms_released': arms_to_release,
        'status': ForceStatus.IDLE.value
    })


@app.route('/readings/<string:arm>', methods=['GET'])
def get_readings(arm):
    if arm not in force_state:
        return jsonify({'error': 'Invalid arm'}), 404
    
    state = force_state[arm]
    return jsonify({
        'arm': arm,
        'status': state['status'].value,
        'current_force': round(state['current_force'], 2)
    })


@app.route('/thresholds', methods=['GET'])
def get_thresholds():
    return jsonify(SAFETY_THRESHOLDS)


@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'left': {
            'status': force_state['left']['status'].value,
            'current_force': round(force_state['left']['current_force'], 2),
            'emergency_stop': force_state['left']['emergency_stop']
        },
        'right': {
            'status': force_state['right']['status'].value,
            'current_force': round(force_state['right']['current_force'], 2),
            'emergency_stop': force_state['right']['emergency_stop']
        },
        'thresholds': SAFETY_THRESHOLDS
    })


@app.route('/emergency_stop', methods=['POST'])
def emergency_stop():
    for arm in force_state:
        force_state[arm]['emergency_stop'] = True
        force_state[arm]['status'] = ForceStatus.EMERGENCY_STOP
        force_state[arm]['current_force'] = 0.0
    
    return jsonify({'message': 'Force control emergency stop activated'})


@app.route('/reset', methods=['POST'])
def reset():
    for arm in force_state:
        force_state[arm]['emergency_stop'] = False
        force_state[arm]['status'] = ForceStatus.IDLE
        force_state[arm]['current_force'] = 0.0
    
    return jsonify({'message': 'Force control system reset'})


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
