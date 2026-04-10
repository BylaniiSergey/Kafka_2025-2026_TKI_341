import os
from flask import Flask, jsonify, request
import threading
from werkzeug.exceptions import HTTPException
from enum import Enum

HOST = '0.0.0.0'
PORT = 8004
MODULE_NAME = os.getenv('MODULE_NAME', 'middle_arm_system')
app = Flask(__name__)
 

class JointStatus(Enum):
    IDLE = "idle"
    MOVING = "moving"
    AT_LIMIT = "at_limit"
    ERROR = "error"


JOINTS_CONFIG = {
    'elbow_flexion': {'min_angle': 0, 'max_angle': 145, 'max_speed': 80},
    'forearm_pronation': {'min_angle': -80, 'max_angle': 80, 'max_speed': 60}
}


joint_states = {
    'left': {
        'status': JointStatus.IDLE,
        'positions': {joint: 0.0 for joint in JOINTS_CONFIG}
    },
    'right': {
        'status': JointStatus.IDLE,
        'positions': {joint: 0.0 for joint in JOINTS_CONFIG}
    }
}


@app.route('/move', methods=['POST'])
def move():
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    arm = data.get('arm')
    intent = data.get('intent')
    strength = data.get('strength', 0.5)
    speed_modifier = data.get('speed_modifier', 1.0)
    
    if arm not in joint_states:
        return jsonify({'error': 'Invalid arm specified'}), 400
    
    state = joint_states[arm]
    state['status'] = JointStatus.MOVING
    
    target_changes = calculate_movement(intent, strength)
    
    new_positions = {}
    for joint, change in target_changes.items():
        if joint in state['positions']:
            current = state['positions'][joint]
            config = JOINTS_CONFIG[joint]
            
            new_angle = current + change * speed_modifier
            new_angle = max(config['min_angle'], min(config['max_angle'], new_angle))
            
            state['positions'][joint] = new_angle
            new_positions[joint] = new_angle
    
    state['status'] = JointStatus.IDLE
    
    return jsonify({
        'success': True,
        'arm': arm,
        'intent': intent,
        'positions': new_positions
    })


def calculate_movement(intent, strength):
    base_movement = 45 * strength
    
    movements = {
        'flex_elbow': {
            'elbow_flexion': base_movement
        },
        'extend_elbow': {
            'elbow_flexion': -base_movement
        },
        'extend_arm': {
            'elbow_flexion': -base_movement * 0.5
        },
        'retract_arm': {
            'elbow_flexion': base_movement * 0.7
        },
        'pronate': {
            'forearm_pronation': base_movement
        },
        'supinate': {
            'forearm_pronation': -base_movement
        }
    }
    
    return movements.get(intent, {})


@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'left': {
            'status': joint_states['left']['status'].value,
            'positions': joint_states['left']['positions']
        },
        'right': {
            'status': joint_states['right']['status'].value,
            'positions': joint_states['right']['positions']
        }
    })


@app.route('/positions/<string:arm>', methods=['GET'])
def get_positions(arm):
    if arm not in joint_states:
        return jsonify({'error': 'Invalid arm'}), 404
    
    return jsonify({
        'arm': arm,
        'positions': joint_states[arm]['positions'],
        'status': joint_states[arm]['status'].value
    })


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
