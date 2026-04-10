import os
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from werkzeug.exceptions import HTTPException
from datetime import datetime
from enum import Enum

HOST = '0.0.0.0'
PORT = 9003
MODULE_NAME = os.getenv('MODULE_NAME', 'knee_belt_system')
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///knee_belt.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


class KneeStatus(Enum):
    IDLE = "idle"
    MOVING = "moving"
    FLEXED = "flexed"
    EXTENDED = "extended"
    LOCKED = "locked"
    AT_LIMIT = "at_limit"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


KNEE_CONFIG = {
    'knee_flexion': {
        'min_angle': 0,
        'max_angle': 135,
        'max_speed': 60,
        'lock_angle': 5
    }
}


class KneePosition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    leg = db.Column(db.String(10))
    angle = db.Column(db.Float)
    is_locked = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


knee_states = {
    'left': {
        'status': KneeStatus.IDLE,
        'angle': 0.0,
        'is_locked': False,
        'emergency_stop': False
    },
    'right': {
        'status': KneeStatus.IDLE,
        'angle': 0.0,
        'is_locked': False,
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
    
    leg = data.get('leg', 'both')
    intent = data.get('intent')
    strength = data.get('strength', 0.5)
    speed_modifier = data.get('speed_modifier', 1.0)
    
    legs_to_move = ['left', 'right'] if leg == 'both' else [leg]
    results = {}
    
    for current_leg in legs_to_move:
        if current_leg not in knee_states:
            continue
        
        if knee_states[current_leg]['emergency_stop']:
            results[current_leg] = {'error': 'Emergency stop is active'}
            continue
        
        state = knee_states[current_leg]
        state['status'] = KneeStatus.MOVING
        
        if state['is_locked'] and intent not in ['stand_up']:
            state['is_locked'] = False
        
        target_change = calculate_knee_movement(intent, strength)
        config = KNEE_CONFIG['knee_flexion']
        
        new_angle = state['angle'] + target_change * speed_modifier
        new_angle = max(config['min_angle'], min(config['max_angle'], new_angle))
        
        state['angle'] = new_angle
        
        if new_angle <= config['lock_angle']:
            state['status'] = KneeStatus.EXTENDED
            if intent == 'stand_up':
                state['is_locked'] = True
                state['status'] = KneeStatus.LOCKED
        elif new_angle >= config['max_angle'] - 5:
            state['status'] = KneeStatus.FLEXED
        else:
            state['status'] = KneeStatus.IDLE
        
        pos_record = KneePosition(
            leg=current_leg,
            angle=new_angle,
            is_locked=state['is_locked']
        )
        db.session.add(pos_record)
        
        results[current_leg] = {
            'angle': round(new_angle, 2),
            'is_locked': state['is_locked'],
            'status': state['status'].value
        }
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'intent': intent,
        'results': results
    })


def calculate_knee_movement(intent, strength):
    base_movement = 30 * strength
    
    movements = {
        'flex_knee': base_movement,
        'extend_knee': -base_movement,
        'squat': base_movement * 1.5,
        'stand_up': -base_movement * 2,
        'sit_down': base_movement * 1.2,
        'brake': 0
    }
    
    return movements.get(intent, 0)


@app.route('/lock', methods=['POST'])
def lock_knees():
    data = request.json or {}
    leg = data.get('leg', 'both')
    
    legs_to_lock = ['left', 'right'] if leg == 'both' else [leg]
    
    for current_leg in legs_to_lock:
        if current_leg in knee_states:
            state = knee_states[current_leg]
            if state['angle'] <= KNEE_CONFIG['knee_flexion']['lock_angle'] + 10:
                state['is_locked'] = True
                state['status'] = KneeStatus.LOCKED
    
    return jsonify({
        'success': True,
        'locked_legs': legs_to_lock,
        'states': {leg: {'locked': knee_states[leg]['is_locked']} for leg in legs_to_lock}
    })


@app.route('/unlock', methods=['POST'])
def unlock_knees():
    data = request.json or {}
    leg = data.get('leg', 'both')
    
    legs_to_unlock = ['left', 'right'] if leg == 'both' else [leg]
    
    for current_leg in legs_to_unlock:
        if current_leg in knee_states:
            knee_states[current_leg]['is_locked'] = False
            knee_states[current_leg]['status'] = KneeStatus.IDLE
    
    return jsonify({
        'success': True,
        'unlocked_legs': legs_to_unlock
    })


@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'left': {
            'status': knee_states['left']['status'].value,
            'angle': round(knee_states['left']['angle'], 2),
            'is_locked': knee_states['left']['is_locked'],
            'emergency_stop': knee_states['left']['emergency_stop']
        },
        'right': {
            'status': knee_states['right']['status'].value,
            'angle': round(knee_states['right']['angle'], 2),
            'is_locked': knee_states['right']['is_locked'],
            'emergency_stop': knee_states['right']['emergency_stop']
        },
        'config': KNEE_CONFIG
    })


@app.route('/positions/<string:leg>', methods=['GET'])
def get_positions(leg):
    if leg not in knee_states:
        return jsonify({'error': 'Invalid leg'}), 404
    
    state = knee_states[leg]
    return jsonify({
        'leg': leg,
        'angle': round(state['angle'], 2),
        'is_locked': state['is_locked'],
        'status': state['status'].value
    })


@app.route('/emergency_stop', methods=['POST'])
def emergency_stop():
    for leg in knee_states:
        knee_states[leg]['emergency_stop'] = True
        knee_states[leg]['status'] = KneeStatus.EMERGENCY_STOP
        knee_states[leg]['is_locked'] = True
    
    return jsonify({'message': 'Knee system emergency stop activated'})


@app.route('/reset', methods=['POST'])
def reset():
    for leg in knee_states:
        knee_states[leg]['emergency_stop'] = False
        knee_states[leg]['status'] = KneeStatus.IDLE
        knee_states[leg]['is_locked'] = False
    
    return jsonify({'message': 'Knee system reset'})


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