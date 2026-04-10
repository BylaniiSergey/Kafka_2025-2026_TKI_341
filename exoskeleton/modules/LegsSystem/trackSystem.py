import os
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from werkzeug.exceptions import HTTPException
from datetime import datetime
from enum import Enum

HOST = '0.0.0.0'
PORT = 9004
MODULE_NAME = os.getenv('MODULE_NAME', 'track_system')
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///track_system.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


class TrackStatus(Enum):
    IDLE = "idle"
    MOVING_FORWARD = "moving_forward"
    MOVING_BACKWARD = "moving_backward"
    TURNING = "turning"
    PIVOTING = "pivoting"
    BRAKING = "braking"
    STOPPED = "stopped"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


class DriveMode(Enum):
    NORMAL = "normal"
    SLOW = "slow"
    FAST = "fast"
    TERRAIN = "terrain"
    INDOOR = "indoor"


TRACK_CONFIG = {
    'max_speed': 5.0,
    'max_reverse_speed': 3.0,
    'acceleration': 0.5,
    'deceleration': 1.0,
    'turn_radius_min': 0.5,
    'track_width': 0.4,
    'track_length': 0.6
}


class TrackTelemetry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    left_speed = db.Column(db.Float)
    right_speed = db.Column(db.Float)
    direction = db.Column(db.String(20))
    mode = db.Column(db.String(20))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


track_state = {
    'left_track': {
        'speed': 0.0,
        'target_speed': 0.0,
        'direction': 'stopped',
        'motor_load': 0.0
    },
    'right_track': {
        'speed': 0.0,
        'target_speed': 0.0,
        'direction': 'stopped',
        'motor_load': 0.0
    },
    'status': TrackStatus.IDLE,
    'mode': DriveMode.NORMAL,
    'emergency_stop': False,
    'odometer': 0.0
}


with app.app_context():
    db.create_all()


@app.route('/move', methods=['POST'])
def move():
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    if track_state['emergency_stop']:
        return jsonify({'error': 'Emergency stop is active'}), 403
    
    intent = data.get('intent')
    strength = data.get('strength', 0.5)
    speed_modifier = data.get('speed_modifier', 1.0)
    
    left_speed, right_speed, status = calculate_track_speeds(intent, strength, speed_modifier)
    
    track_state['left_track']['target_speed'] = left_speed
    track_state['left_track']['speed'] = left_speed
    track_state['left_track']['direction'] = get_direction(left_speed)
    
    track_state['right_track']['target_speed'] = right_speed
    track_state['right_track']['speed'] = right_speed
    track_state['right_track']['direction'] = get_direction(right_speed)
    
    track_state['status'] = status
    
    telemetry = TrackTelemetry(
        left_speed=left_speed,
        right_speed=right_speed,
        direction=intent,
        mode=track_state['mode'].value
    )
    db.session.add(telemetry)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'intent': intent,
        'left_track': {
            'speed': round(left_speed, 2),
            'direction': track_state['left_track']['direction']
        },
        'right_track': {
            'speed': round(right_speed, 2),
            'direction': track_state['right_track']['direction']
        },
        'status': status.value
    })


def calculate_track_speeds(intent, strength, speed_modifier):
    max_speed = TRACK_CONFIG['max_speed'] * strength * speed_modifier / 3.6
    max_reverse = TRACK_CONFIG['max_reverse_speed'] * strength * speed_modifier / 3.6
    
    if intent == 'move_forward':
        return max_speed, max_speed, TrackStatus.MOVING_FORWARD
    elif intent == 'move_backward':
        return -max_reverse, -max_reverse, TrackStatus.MOVING_BACKWARD
    elif intent == 'turn_left':
        return max_speed * 0.3, max_speed, TrackStatus.TURNING
    elif intent == 'turn_right':
        return max_speed, max_speed * 0.3, TrackStatus.TURNING
    elif intent == 'pivot_left':
        return -max_speed * 0.5, max_speed * 0.5, TrackStatus.PIVOTING
    elif intent == 'pivot_right':
        return max_speed * 0.5, -max_speed * 0.5, TrackStatus.PIVOTING
    elif intent == 'stop':
        return 0.0, 0.0, TrackStatus.STOPPED
    elif intent == 'brake':
        return 0.0, 0.0, TrackStatus.BRAKING
    else:
        return 0.0, 0.0, TrackStatus.IDLE


def get_direction(speed):
    if speed > 0.01:
        return 'forward'
    elif speed < -0.01:
        return 'backward'
    return 'stopped'


@app.route('/set_mode', methods=['POST'])
def set_mode():
    data = request.json or {}
    mode_name = data.get('mode', 'normal')
    
    try:
        track_state['mode'] = DriveMode(mode_name)
        return jsonify({
            'success': True,
            'mode': track_state['mode'].value
        })
    except ValueError:
        return jsonify({'error': f'Unknown mode: {mode_name}'}), 400


@app.route('/speed', methods=['POST'])
def set_speed():
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    if track_state['emergency_stop']:
        return jsonify({'error': 'Emergency stop is active'}), 403
    
    left_speed = data.get('left_speed', 0.0)
    right_speed = data.get('right_speed', 0.0)
    
    max_speed = TRACK_CONFIG['max_speed'] / 3.6
    left_speed = max(-max_speed, min(max_speed, left_speed))
    right_speed = max(-max_speed, min(max_speed, right_speed))
    
    track_state['left_track']['speed'] = left_speed
    track_state['left_track']['target_speed'] = left_speed
    track_state['left_track']['direction'] = get_direction(left_speed)
    
    track_state['right_track']['speed'] = right_speed
    track_state['right_track']['target_speed'] = right_speed
    track_state['right_track']['direction'] = get_direction(right_speed)
    
    if left_speed == 0 and right_speed == 0:
        track_state['status'] = TrackStatus.STOPPED
    elif left_speed == right_speed:
        track_state['status'] = TrackStatus.MOVING_FORWARD if left_speed > 0 else TrackStatus.MOVING_BACKWARD
    else:
        track_state['status'] = TrackStatus.TURNING
    
    return jsonify({
        'success': True,
        'left_speed': round(left_speed, 2),
        'right_speed': round(right_speed, 2),
        'status': track_state['status'].value
    })


@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'status': track_state['status'].value,
        'mode': track_state['mode'].value,
        'emergency_stop': track_state['emergency_stop'],
        'left_track': {
            'speed': round(track_state['left_track']['speed'], 2),
            'target_speed': round(track_state['left_track']['target_speed'], 2),
            'direction': track_state['left_track']['direction'],
            'motor_load': track_state['left_track']['motor_load']
        },
        'right_track': {
            'speed': round(track_state['right_track']['speed'], 2),
            'target_speed': round(track_state['right_track']['target_speed'], 2),
            'direction': track_state['right_track']['direction'],
            'motor_load': track_state['right_track']['motor_load']
        },
        'odometer': round(track_state['odometer'], 2),
        'config': TRACK_CONFIG
    })


@app.route('/telemetry', methods=['GET'])
def get_telemetry():
    limit = request.args.get('limit', 100, type=int)
    records = TrackTelemetry.query.order_by(TrackTelemetry.timestamp.desc()).limit(limit).all()
    
    return jsonify([{
        'id': r.id,
        'left_speed': r.left_speed,
        'right_speed': r.right_speed,
        'direction': r.direction,
        'mode': r.mode,
        'timestamp': r.timestamp.strftime('%Y-%m-%d %H:%M:%S')
    } for r in records])


@app.route('/emergency_stop', methods=['POST'])
def emergency_stop():
    track_state['emergency_stop'] = True
    track_state['status'] = TrackStatus.EMERGENCY_STOP
    track_state['left_track']['speed'] = 0.0
    track_state['left_track']['target_speed'] = 0.0
    track_state['left_track']['direction'] = 'stopped'
    track_state['right_track']['speed'] = 0.0
    track_state['right_track']['target_speed'] = 0.0
    track_state['right_track']['direction'] = 'stopped'
    
    return jsonify({'message': 'Track system emergency stop activated'})


@app.route('/reset', methods=['POST'])
def reset():
    track_state['emergency_stop'] = False
    track_state['status'] = TrackStatus.IDLE
    track_state['mode'] = DriveMode.NORMAL
    track_state['left_track']['speed'] = 0.0
    track_state['left_track']['target_speed'] = 0.0
    track_state['left_track']['direction'] = 'stopped'
    track_state['right_track']['speed'] = 0.0
    track_state['right_track']['target_speed'] = 0.0
    track_state['right_track']['direction'] = 'stopped'
    
    return jsonify({'message': 'Track system reset'})


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