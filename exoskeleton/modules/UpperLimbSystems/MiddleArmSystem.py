import os
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
import threading
from werkzeug.exceptions import HTTPException
from datetime import datetime
from enum import Enum

HOST = '0.0.0.0'
PORT = 8004
MODULE_NAME = os.getenv('MODULE_NAME', 'middle_arm_system')
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///middle_arm.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


class JointStatus(Enum):
    IDLE = "idle"
    MOVING = "moving"
    AT_LIMIT = "at_limit"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


JOINTS_CONFIG = {
    'elbow_flexion': {'min_angle': 0, 'max_angle': 145, 'max_speed': 80},
    'forearm_pronation': {'min_angle': -80, 'max_angle': 80, 'max_speed': 60},
    'wrist_flexion': {'min_angle': -70, 'max_angle': 70, 'max_speed': 50},
    'wrist_deviation': {'min_angle': -20, 'max_angle': 35, 'max_speed': 40}
}


class JointPosition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    arm = db.Column(db.String(10))
    joint_name = db.Column(db.String(50))
    angle = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class MovementExecution(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    arm = db.Column(db.String(10))
    intent = db.Column(db.String(50))
    start_positions = db.Column(db.String(200))
    end_positions = db.Column(db.String(200))
    strength = db.Column(db.Float)
    success = db.Column(db.Boolean)
    executed_at = db.Column(db.DateTime, default=datetime.utcnow)


joint_states = {
    'left': {
        'status': JointStatus.IDLE,
        'positions': {joint: 0.0 for joint in JOINTS_CONFIG},
        'emergency_stop': False
    },
    'right': {
        'status': JointStatus.IDLE,
        'positions': {joint: 0.0 for joint in JOINTS_CONFIG},
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
    speed_modifier = data.get('speed_modifier', 1.0)
    
    if arm not in joint_states:
        return jsonify({'error': 'Invalid arm specified'}), 400
    
    if joint_states[arm]['emergency_stop']:
        return jsonify({'error': 'Emergency stop is active'}), 403
    
    state = joint_states[arm]
    state['status'] = JointStatus.MOVING
    
    start_positions = state['positions'].copy()
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
            
            pos_record = JointPosition(arm=arm, joint_name=joint, angle=new_angle)
            db.session.add(pos_record)
    
    state['status'] = JointStatus.IDLE
    
    execution = MovementExecution(
        arm=arm,
        intent=intent,
        start_positions=str(start_positions),
        end_positions=str(new_positions),
        strength=strength,
        success=True
    )
    db.session.add(execution)
    db.session.commit()
    
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
            'elbow_flexion': -base_movement * 0.5,
            'wrist_flexion': base_movement * 0.2
        },
        'retract_arm': {
            'elbow_flexion': base_movement * 0.7
        }
    }
    
    return movements.get(intent, {})


@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'left': {
            'status': joint_states['left']['status'].value,
            'positions': joint_states['left']['positions'],
            'emergency_stop': joint_states['left']['emergency_stop']
        },
        'right': {
            'status': joint_states['right']['status'].value,
            'positions': joint_states['right']['positions'],
            'emergency_stop': joint_states['right']['emergency_stop']
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


@app.route('/emergency_stop', methods=['POST'])
def emergency_stop():
    for arm in joint_states:
        joint_states[arm]['emergency_stop'] = True
        joint_states[arm]['status'] = JointStatus.EMERGENCY_STOP
    
    return jsonify({'message': 'Middle arm emergency stop activated'})


@app.route('/reset', methods=['POST'])
def reset():
    for arm in joint_states:
        joint_states[arm]['emergency_stop'] = False
        joint_states[arm]['status'] = JointStatus.IDLE
    
    return jsonify({'message': 'Middle arm system reset'})


@app.route('/history', methods=['GET'])
def get_history():
    limit = request.args.get('limit', 50, type=int)
    records = MovementExecution.query.order_by(MovementExecution.executed_at.desc()).limit(limit).all()
    return jsonify([{
        'id': r.id,
        'arm': r.arm,
        'intent': r.intent,
        'strength': r.strength,
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