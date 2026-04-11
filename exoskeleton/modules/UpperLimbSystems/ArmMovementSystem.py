import requests
import os
from flask import Flask, jsonify, request
import threading
from werkzeug.exceptions import HTTPException
from enum import Enum
import time

HOST = '0.0.0.0'
PORT = 8002
MODULE_NAME = os.getenv('MODULE_NAME', 'arm_movement_system')
app = Flask(__name__)

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


arm_state = {
    'left': {'status': ArmStatus.IDLE, 'position': {}},
    'right': {'status': ArmStatus.IDLE, 'position': {}},
    'emergency_stop': False
}


@app.route('/execute', methods=['POST'])
def execute_movement():
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
    arms_to_move = ['left', 'right'] if arm == 'both' else [arm]
    
    for current_arm in arms_to_move:
        if current_arm not in arm_state:
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
                requests.post(f'{url}/move', json=command, timeout=REQUEST_TIMEOUT)
            except requests.RequestException:
                pass
        
        arm_state[current_arm]['status'] = ArmStatus.IDLE
    
    return '', 204  # No Content


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
