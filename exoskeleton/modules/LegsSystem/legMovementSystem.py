import requests
import os
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException
from enum import Enum

HOST = '0.0.0.0'
PORT = 9002
MODULE_NAME = os.getenv('MODULE_NAME', 'leg_movement_system')
app = Flask(__name__)

KNEE_SYSTEM_URL = 'http://knee_belt_system:9003'
TRACK_SYSTEM_URL = 'http://track_system:9004'
FORCE_CONTROL_URL = 'http://leg_force_control:9006'
REQUEST_TIMEOUT = 5

INTENT_MAPPING = {
    'flex_knee': ['knee'],
    'extend_knee': ['knee'],
    'squat': ['knee'],
    'stand_up': ['knee'],
    'sit_down': ['knee'],
    'move_forward': ['track'],
    'move_backward': ['track'],
    'turn_left': ['track'],
    'turn_right': ['track'],
    'pivot_left': ['track'],
    'pivot_right': ['track'],
    'stop': ['track'],
    'brake': ['track', 'knee']
}


class SystemStatus(Enum):
    IDLE = "idle"
    MOVING = "moving"
    STANDING = "standing"
    DRIVING = "driving"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


system_state = {
    'status': SystemStatus.IDLE,
    'emergency_stop': False,
    'current_intent': None
}


@app.route('/execute', methods=['POST'])
def execute_movement():
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    if system_state['emergency_stop']:
        return jsonify({'error': 'Emergency stop is active'}), 403
    
    leg = data.get('leg', 'both')
    intent = data.get('intent')
    strength = data.get('strength', 0.5)
    speed_modifier = data.get('speed_modifier', 1.0)
    
    if not intent:
        return jsonify({'error': 'intent is required'}), 400
    
    if intent not in INTENT_MAPPING:
        return jsonify({'error': f'Unknown intent: {intent}'}), 400
    
    systems = INTENT_MAPPING[intent]
    system_state['status'] = SystemStatus.MOVING
    system_state['current_intent'] = intent
    
    results = {}
    
    for system in systems:
        url = get_system_url(system)
        command = {
            'leg': leg,
            'intent': intent,
            'strength': strength,
            'speed_modifier': speed_modifier
        }
        
        try:
            response = requests.post(f'{url}/move', json=command, timeout=REQUEST_TIMEOUT)
            results[system] = response.json() if response.ok else {'error': 'Request failed'}
        except requests.RequestException as e:
            results[system] = {'error': str(e)}
    
    if intent in ['move_forward', 'move_backward', 'turn_left', 'turn_right']:
        system_state['status'] = SystemStatus.DRIVING
    elif intent == 'stand_up':
        system_state['status'] = SystemStatus.STANDING
    else:
        system_state['status'] = SystemStatus.IDLE
    
    return jsonify({
        'success': True,
        'intent': intent,
        'systems_called': systems,
        'results': results
    })


@app.route('/emergency_stop', methods=['POST'])
def emergency_stop():
    system_state['emergency_stop'] = True
    system_state['status'] = SystemStatus.EMERGENCY_STOP
    
    for url in [KNEE_SYSTEM_URL, TRACK_SYSTEM_URL, FORCE_CONTROL_URL]:
        try:
            requests.post(f'{url}/emergency_stop', timeout=REQUEST_TIMEOUT)
        except:
            pass
    
    return jsonify({'message': 'Emergency stop activated for all systems'})


@app.route('/reset', methods=['POST'])
def reset():
    system_state['emergency_stop'] = False
    system_state['status'] = SystemStatus.IDLE
    system_state['current_intent'] = None
    
    for url in [KNEE_SYSTEM_URL, TRACK_SYSTEM_URL, FORCE_CONTROL_URL]:
        try:
            requests.post(f'{url}/reset', timeout=REQUEST_TIMEOUT)
        except:
            pass
    
    return jsonify({'message': 'All systems reset complete'})


@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'main_status': system_state['status'].value,
        'emergency_stop': system_state['emergency_stop'],
        'current_intent': system_state['current_intent']
    })


def get_system_url(system):
    urls = {
        'knee': KNEE_SYSTEM_URL,
        'track': TRACK_SYSTEM_URL
    }
    return urls.get(system)


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