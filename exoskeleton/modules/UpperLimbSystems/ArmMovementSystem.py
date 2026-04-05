import requests
import os
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
import threading
from werkzeug.exceptions import HTTPException
from datetime import datetime
from enum import Enum
import time

HOST = '0.0.0.0'
PORT = 8002
MODULE_NAME = os.getenv('MODULE_NAME', 'arm_movement_system')
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///arm_movement.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

UPPER_ARM_URL = 'http://upper_arm_system:8003'
MIDDLE_ARM_URL = 'http://middle_arm_system:8004'
FINGERS_URL = 'http://fingers_system:8005'
REQUEST_TIMEOUT = 5

# Маппинг интентов на отделы руки
INTENT_MAPPING = {
    'lift_arm': ['upper'],
    'lower_arm': ['upper'],
    'extend_arm': ['upper', 'middle'],
    'retract_arm': ['upper', 'middle'],
    'flex_elbow': ['middle'],
    'extend_elbow': ['middle'],
    'grasp': ['fingers'],
    'release': ['fingers']
}


class ArmStatus(Enum):
    IDLE = "idle"
    MOVING = "moving"
    HOLDING = "holding"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


class MovementRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    arm = db.Column(db.String(20))
    intent = db.Column(db.String(50))
    strength = db.Column(db.Float)
    speed_modifier = db.Column(db.Float)
    sections_involved = db.Column(db.String(100))
    success = db.Column(db.Boolean)
    error_message = db.Column(db.String(200))
    executed_at = db.Column(db.DateTime, default=datetime.utcnow)
    duration_ms = db.Column(db.Integer)


arm_state = {
    'left': {'status': ArmStatus.IDLE, 'position': {}},
    'right': {'status': ArmStatus.IDLE, 'position': {}},
    'emergency_stop': False
}


with app.app_context():
    db.create_all()


@app.route('/execute', methods=['POST'])
def execute_movement():
    if arm_state['emergency_stop']:
        return jsonify({'error': 'Emergency stop is active'}), 403
    
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    arm = data.get('arm')
    intent = data.get('intent')
    strength = data.get('strength', 0.5)
    speed_modifier = data.get('speed_modifier', 1.0)
    
    if not arm or not intent:
        return jsonify({'error': 'arm and intent are required'}), 400
    
    if intent not in INTENT_MAPPING:
        return jsonify({'error': f'Unknown intent: {intent}'}), 400
    
    sections = INTENT_MAPPING[intent]
    start_time = time.time()
    results = {}
    errors = []
    
    arms_to_move = ['left', 'right'] if arm == 'both' else [arm]
    
    for current_arm in arms_to_move:
        if current_arm not in arm_state:
            errors.append(f'Invalid arm: {current_arm}')
            continue
            
        arm_state[current_arm]['status'] = ArmStatus.MOVING
        
        for section in sections:
            url = get_section_url(section)
            command = {
                'arm': current_arm,
                'intent': intent,
                'strength': strength,
                'speed_modifier': speed_modifier
            }
            
            try:
                response = requests.post(f'{url}/move', json=command, timeout=REQUEST_TIMEOUT)
                if response.status_code == 200:
                    results[f'{current_arm}_{section}'] = response.json()
                else:
                    errors.append(f'{current_arm}_{section}: {response.json().get("error", "Unknown error")}')
            except requests.RequestException as e:
                errors.append(f'{current_arm}_{section}: {str(e)}')
        
        arm_state[current_arm]['status'] = ArmStatus.IDLE if not errors else ArmStatus.ERROR
    
    duration_ms = int((time.time() - start_time) * 1000)
    
    record = MovementRecord(
        arm=arm,
        intent=intent,
        strength=strength,
        speed_modifier=speed_modifier,
        sections_involved=','.join(sections),
        success=len(errors) == 0,
        error_message='; '.join(errors) if errors else None,
        duration_ms=duration_ms
    )
    db.session.add(record)
    db.session.commit()
    
    if errors:
        return jsonify({
            'success': False,
            'errors': errors,
            'partial_results': results,
            'duration_ms': duration_ms
        }), 500
    
    return jsonify({
        'success': True,
        'results': results,
        'duration_ms': duration_ms
    })


@app.route('/status', methods=['GET'])
def get_status():
    subsystem_status = {}
    
    for section, url in [('upper', UPPER_ARM_URL), ('middle', MIDDLE_ARM_URL), ('fingers', FINGERS_URL)]:
        try:
            response = requests.get(f'{url}/status', timeout=REQUEST_TIMEOUT)
            subsystem_status[section] = response.json() if response.status_code == 200 else {'status': 'error'}
        except:
            subsystem_status[section] = {'status': 'offline'}
    
    return jsonify({
        'arm_state': {
            'left': arm_state['left']['status'].value,
            'right': arm_state['right']['status'].value
        },
        'emergency_stop': arm_state['emergency_stop'],
        'subsystems': subsystem_status
    })


@app.route('/emergency_stop', methods=['POST'])
def emergency_stop():
    arm_state['emergency_stop'] = True
    arm_state['left']['status'] = ArmStatus.EMERGENCY_STOP
    arm_state['right']['status'] = ArmStatus.EMERGENCY_STOP
    
    for url in [UPPER_ARM_URL, MIDDLE_ARM_URL, FINGERS_URL]:
        try:
            requests.post(f'{url}/emergency_stop', timeout=REQUEST_TIMEOUT)
        except:
            pass
    
    return jsonify({'message': 'Emergency stop activated'})


@app.route('/reset', methods=['POST'])
def reset():
    arm_state['emergency_stop'] = False
    arm_state['left']['status'] = ArmStatus.IDLE
    arm_state['right']['status'] = ArmStatus.IDLE
    
    for url in [UPPER_ARM_URL, MIDDLE_ARM_URL, FINGERS_URL]:
        try:
            requests.post(f'{url}/reset', timeout=REQUEST_TIMEOUT)
        except:
            pass
    
    return jsonify({'message': 'System reset complete'})


@app.route('/history', methods=['GET'])
def get_history():
    limit = request.args.get('limit', 100, type=int)
    records = MovementRecord.query.order_by(MovementRecord.executed_at.desc()).limit(limit).all()
    return jsonify([{
        'id': r.id,
        'arm': r.arm,
        'intent': r.intent,
        'strength': r.strength,
        'success': r.success,
        'duration_ms': r.duration_ms,
        'executed_at': r.executed_at.strftime('%Y-%m-%d %H:%M:%S')
    } for r in records])


def get_section_url(section):
    urls = {
        'upper': UPPER_ARM_URL,
        'middle': MIDDLE_ARM_URL,
        'fingers': FINGERS_URL
    }
    return urls.get(section)


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
