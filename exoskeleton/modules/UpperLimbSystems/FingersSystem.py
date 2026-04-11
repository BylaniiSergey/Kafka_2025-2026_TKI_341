import requests
import os
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
import threading
from werkzeug.exceptions import HTTPException
from datetime import datetime
from enum import Enum

HOST = '0.0.0.0'
PORT = 8005
MODULE_NAME = os.getenv('MODULE_NAME', 'fingers_system')
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fingers.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

FORCE_CONTROL_URL = 'http://force_control_system:8006'
REQUEST_TIMEOUT = 5


class FingerStatus(Enum):
    IDLE = "idle"
    MOVING = "moving"
    GRASPING = "grasping"
    HOLDING = "holding"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


class GripState(Enum):
    OPEN = "open"
    CLOSED = "closed"
    PARTIAL = "partial"


class GripExecution(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    arm = db.Column(db.String(10))
    intent = db.Column(db.String(50))
    grip_percentage = db.Column(db.Float)
    target_force = db.Column(db.Float)
    actual_force = db.Column(db.Float)
    object_detected = db.Column(db.Boolean)
    success = db.Column(db.Boolean)
    executed_at = db.Column(db.DateTime, default=datetime.utcnow)


# Состояние в памяти
finger_states = {
    'left': {
        'status': FingerStatus.IDLE,
        'grip_percentage': 0.0,
        'grip_state': GripState.OPEN,
        'grip_force': 0.0,
        'emergency_stop': False
    },
    'right': {
        'status': FingerStatus.IDLE,
        'grip_percentage': 0.0,
        'grip_state': GripState.OPEN,
        'grip_force': 0.0,
        'emergency_stop': False
    }
}


with app.app_context():
    db.create_all()


@app.route('/move', methods=['POST'])
def move():
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    arm = data.get('arm')
    intent = data.get('intent')
    strength = data.get('strength', 0.5)
    
    if arm not in finger_states:
        return jsonify({'error': 'Invalid arm specified'}), 400
    
    if finger_states[arm]['emergency_stop']:
        return jsonify({'error': 'Emergency stop is active'}), 403
    
    if intent == 'grasp':
        return execute_grasp(arm, strength)
    elif intent == 'release':
        return execute_release(arm)
    
    return jsonify({'error': f'Unknown intent: {intent}'}), 400


def execute_grasp(arm, strength):
    state = finger_states[arm]
    state['status'] = FingerStatus.GRASPING
    
    # Процент закрытия захвата (0-100%)
    grip_percentage = min(100.0, strength * 100)
    
    # Запрос к системе контроля силы
    force_command = {
        'arm': arm,
        'grip_type': 'power',
        'target_force': strength * 100,
        'max_force': 150
    }
    
    actual_force = 0
    object_detected = False
    
    try:
        force_response = requests.post(
            f'{FORCE_CONTROL_URL}/apply_force',
            json=force_command,
            timeout=REQUEST_TIMEOUT
        )
        if force_response.status_code == 200:
            force_data = force_response.json()
            actual_force = force_data.get('applied_force', 0)
            object_detected = force_data.get('object_detected', False)
    except requests.RequestException:
        actual_force = strength * 50
        object_detected = True
    
    state['grip_percentage'] = grip_percentage
    state['grip_force'] = actual_force
    state['grip_state'] = GripState.CLOSED if grip_percentage > 80 else GripState.PARTIAL
    state['status'] = FingerStatus.HOLDING if object_detected else FingerStatus.IDLE
    
    # Логирование
    grip_record = GripExecution(
        arm=arm,
        intent='grasp',
        grip_percentage=grip_percentage,
        target_force=strength * 100,
        actual_force=actual_force,
        object_detected=object_detected,
        success=object_detected
    )
    db.session.add(grip_record)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'arm': arm,
        'grip_percentage': grip_percentage,
        'grip_force': actual_force,
        'object_detected': object_detected,
        'grip_state': state['grip_state'].value
    })


def execute_release(arm):
    state = finger_states[arm]
    
    # Запрос на отпускание к системе контроля силы
    try:
        requests.post(
            f'{FORCE_CONTROL_URL}/release',
            json={'arm': arm},
            timeout=REQUEST_TIMEOUT
        )
    except:
        pass
    
    state['grip_percentage'] = 0.0
    state['grip_force'] = 0.0
    state['grip_state'] = GripState.OPEN
    state['status'] = FingerStatus.IDLE
    
    # Логирование
    grip_record = GripExecution(
        arm=arm,
        intent='release',
        grip_percentage=0,
        target_force=0,
        actual_force=0,
        object_detected=False,
        success=True
    )
    db.session.add(grip_record)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'arm': arm,
        'grip_percentage': 0,
        'grip_force': 0,
        'grip_state': GripState.OPEN.value
    })


@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'left': {
            'status': finger_states['left']['status'].value,
            'grip_percentage': finger_states['left']['grip_percentage'],
            'grip_state': finger_states['left']['grip_state'].value,
            'grip_force': finger_states['left']['grip_force'],
            'emergency_stop': finger_states['left']['emergency_stop']
        },
        'right': {
            'status': finger_states['right']['status'].value,
            'grip_percentage': finger_states['right']['grip_percentage'],
            'grip_state': finger_states['right']['grip_state'].value,
            'grip_force': finger_states['right']['grip_force'],
            'emergency_stop': finger_states['right']['emergency_stop']
        }
    })


@app.route('/positions/<string:arm>', methods=['GET'])
def get_positions(arm):
    if arm not in finger_states:
        return jsonify({'error': 'Invalid arm'}), 404
    
    state = finger_states[arm]
    return jsonify({
        'arm': arm,
        'grip_percentage': state['grip_percentage'],
        'grip_state': state['grip_state'].value,
        'grip_force': state['grip_force'],
        'status': state['status'].value
    })


@app.route('/emergency_stop', methods=['POST'])
def emergency_stop():
    for arm in finger_states:
        finger_states[arm]['emergency_stop'] = True
        finger_states[arm]['status'] = FingerStatus.EMERGENCY_STOP
    
    try:
        requests.post(f'{FORCE_CONTROL_URL}/emergency_stop', timeout=REQUEST_TIMEOUT)
    except:
        pass
    
    return jsonify({'message': 'Fingers emergency stop activated'})


@app.route('/reset', methods=['POST'])
def reset():
    for arm in finger_states:
        finger_states[arm]['emergency_stop'] = False
        finger_states[arm]['status'] = FingerStatus.IDLE
        finger_states[arm]['grip_percentage'] = 0.0
        finger_states[arm]['grip_state'] = GripState.OPEN
        finger_states[arm]['grip_force'] = 0.0
    
    try:
        requests.post(f'{FORCE_CONTROL_URL}/reset', timeout=REQUEST_TIMEOUT)
    except:
        pass
    
    return jsonify({'message': 'Fingers system reset'})


@app.route('/history', methods=['GET'])
def get_history():
    limit = request.args.get('limit', 50, type=int)
    records = GripExecution.query.order_by(GripExecution.executed_at.desc()).limit(limit).all()
    return jsonify([{
        'id': r.id,
        'arm': r.arm,
        'intent': r.intent,
        'grip_percentage': r.grip_percentage,
        'target_force': r.target_force,
        'actual_force': r.actual_force,
        'object_detected': r.object_detected,
        'success': r.success,
        'executed_at': r.executed_at.strftime('%Y-%m-%d %H:%M:%S')
    } for r in records])


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