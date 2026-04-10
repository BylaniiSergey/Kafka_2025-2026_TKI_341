import os
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from werkzeug.exceptions import HTTPException
from datetime import datetime
from enum import Enum

HOST = '0.0.0.0'
PORT = 9006
MODULE_NAME = os.getenv('MODULE_NAME', 'leg_force_control_system')
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///leg_force_control.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


class ForceStatus(Enum):
    IDLE = "idle"
    APPLYING = "applying"
    SUPPORTING = "supporting"
    DRIVING = "driving"
    RELEASING = "releasing"
    OVERLOAD = "overload"
    EMERGENCY_STOP = "emergency_stop"


class ForceReading(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    location = db.Column(db.String(20))
    force_value = db.Column(db.Float)
    torque_value = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


SAFETY_THRESHOLDS = {
    'knee': {
        'max_safe_torque': 150.0,
        'emergency_threshold': 200.0,
        'standing_torque': 80.0,
        'squat_torque': 120.0
    },
    'track': {
        'max_safe_force': 500.0,
        'emergency_threshold': 800.0,
        'normal_load': 300.0
    }
}


force_state = {
    'left_knee': {
        'status': ForceStatus.IDLE,
        'current_torque': 0.0,
        'emergency_stop': False
    },
    'right_knee': {
        'status': ForceStatus.IDLE,
        'current_torque': 0.0,
        'emergency_stop': False
    },
    'left_track': {
        'status': ForceStatus.IDLE,
        'current_force': 0.0,
        'traction': 0.0,
        'emergency_stop': False
    },
    'right_track': {
        'status': ForceStatus.IDLE,
        'current_force': 0.0,
        'traction': 0.0,
        'emergency_stop': False
    }
}


with app.app_context():
    db.create_all()


@app.route('/apply_knee_torque', methods=['POST'])
def apply_knee_torque():
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    leg = data.get('leg', 'both')
    action = data.get('action', 'support')
    target_torque = data.get('target_torque', SAFETY_THRESHOLDS['knee']['standing_torque'])
    
    legs = ['left_knee', 'right_knee'] if leg == 'both' else [f'{leg}_knee']
    results = {}
    
    for knee in legs:
        if knee not in force_state:
            continue
            
        if force_state[knee]['emergency_stop']:
            results[knee] = {'error': 'Emergency stop is active'}
            continue
        
        state = force_state[knee]
        state['status'] = ForceStatus.APPLYING
        
        safe_torque = min(target_torque, SAFETY_THRESHOLDS['knee']['max_safe_torque'])
        
        if target_torque > SAFETY_THRESHOLDS['knee']['emergency_threshold']:
            state['status'] = ForceStatus.OVERLOAD
            safe_torque = SAFETY_THRESHOLDS['knee']['max_safe_torque']
        
        state['current_torque'] = safe_torque
        state['status'] = ForceStatus.SUPPORTING
        
        reading = ForceReading(
            location=knee,
            force_value=0,
            torque_value=safe_torque
        )
        db.session.add(reading)
        
        results[knee] = {
            'torque': round(safe_torque, 2),
            'status': state['status'].value
        }
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'action': action,
        'results': results
    })


@app.route('/apply_track_force', methods=['POST'])
def apply_track_force():
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    track = data.get('track', 'both')
    target_force = data.get('target_force', SAFETY_THRESHOLDS['track']['normal_load'])
    traction_mode = data.get('traction_mode', 'normal')
    
    tracks = ['left_track', 'right_track'] if track == 'both' else [f'{track}_track']
    results = {}
    
    for track_name in tracks:
        if track_name not in force_state:
            continue
            
        if force_state[track_name]['emergency_stop']:
            results[track_name] = {'error': 'Emergency stop is active'}
            continue
        
        state = force_state[track_name]
        state['status'] = ForceStatus.APPLYING
        
        safe_force = min(target_force, SAFETY_THRESHOLDS['track']['max_safe_force'])
        
        if traction_mode == 'high_grip':
            traction = safe_force * 0.9
        elif traction_mode == 'low_friction':
            traction = safe_force * 0.5
        else:
            traction = safe_force * 0.7
        
        state['current_force'] = safe_force
        state['traction'] = traction
        state['status'] = ForceStatus.DRIVING
        
        reading = ForceReading(
            location=track_name,
            force_value=safe_force,
            torque_value=0
        )
        db.session.add(reading)
        
        results[track_name] = {
            'force': round(safe_force, 2),
            'traction': round(traction, 2),
            'status': state['status'].value
        }
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'traction_mode': traction_mode,
        'results': results
    })


@app.route('/release', methods=['POST'])
def release():
    data = request.json or {}
    location = data.get('location')
    
    if location == 'all' or location is None:
        locations = list(force_state.keys())
    else:
        locations = [location] if location in force_state else []
    
    for loc in locations:
        state = force_state[loc]
        state['status'] = ForceStatus.RELEASING
        if 'knee' in loc:
            state['current_torque'] = 0.0
        else:
            state['current_force'] = 0.0
            state['traction'] = 0.0
        state['status'] = ForceStatus.IDLE
    
    return jsonify({
        'success': True,
        'released': locations
    })


@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'left_knee': {
            'status': force_state['left_knee']['status'].value,
            'current_torque': round(force_state['left_knee']['current_torque'], 2),
            'emergency_stop': force_state['left_knee']['emergency_stop']
        },
        'right_knee': {
            'status': force_state['right_knee']['status'].value,
            'current_torque': round(force_state['right_knee']['current_torque'], 2),
            'emergency_stop': force_state['right_knee']['emergency_stop']
        },
        'left_track': {
            'status': force_state['left_track']['status'].value,
            'current_force': round(force_state['left_track']['current_force'], 2),
            'traction': round(force_state['left_track']['traction'], 2),
            'emergency_stop': force_state['left_track']['emergency_stop']
        },
        'right_track': {
            'status': force_state['right_track']['status'].value,
            'current_force': round(force_state['right_track']['current_force'], 2),
            'traction': round(force_state['right_track']['traction'], 2),
            'emergency_stop': force_state['right_track']['emergency_stop']
        },
        'thresholds': SAFETY_THRESHOLDS
    })


@app.route('/thresholds', methods=['GET'])
def get_thresholds():
    return jsonify(SAFETY_THRESHOLDS)


@app.route('/emergency_stop', methods=['POST'])
def emergency_stop():
    for location in force_state:
        force_state[location]['emergency_stop'] = True
        force_state[location]['status'] = ForceStatus.EMERGENCY_STOP
        if 'knee' in location:
            force_state[location]['current_torque'] = 0.0
        else:
            force_state[location]['current_force'] = 0.0
            force_state[location]['traction'] = 0.0
    
    return jsonify({'message': 'Force control emergency stop activated'})


@app.route('/reset', methods=['POST'])
def reset():
    for location in force_state:
        force_state[location]['emergency_stop'] = False
        force_state[location]['status'] = ForceStatus.IDLE
        if 'knee' in location:
            force_state[location]['current_torque'] = 0.0
        else:
            force_state[location]['current_force'] = 0.0
            force_state[location]['traction'] = 0.0
    
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


if __name__ == '__main__':
    app.run(host=HOST, port=PORT, debug=False)