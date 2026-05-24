# control_system/main.py
import os
import sys
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float,
    String, Boolean, DateTime, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kafka_bus import EventBus, TOPIC_COMMANDS, TOPIC_TELEMETRY

HOST        = '0.0.0.0'
PORT        = 8000
MODULE_NAME = os.getenv('MODULE_NAME', 'control_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

# ── URL подсистем ─────────────────────────────────────────────────────────────

NEURAL_SIGNAL_URL = os.getenv('NEURAL_SIGNAL_URL', 'http://localhost:8001')
ARM_MOVEMENT_URL  = os.getenv('ARM_MOVEMENT_URL',  'http://localhost:8002')
UPPER_ARM_URL     = os.getenv('UPPER_ARM_URL',     'http://localhost:8003')
MIDDLE_ARM_URL    = os.getenv('MIDDLE_ARM_URL',    'http://localhost:8004')
FINGERS_URL       = os.getenv('FINGERS_URL',       'http://localhost:8005')
FORCE_CONTROL_URL = os.getenv('FORCE_CONTROL_URL', 'http://localhost:8006')

LEG_NEURAL_URL   = os.getenv('LEG_NEURAL_URL',   'http://localhost:9001')
LEG_MOVEMENT_URL = os.getenv('LEG_MOVEMENT_URL', 'http://localhost:9002')
KNEE_BELT_URL    = os.getenv('KNEE_BELT_URL',    'http://localhost:9003')
TRACK_SYSTEM_URL = os.getenv('TRACK_SYSTEM_URL', 'http://localhost:9004')
LEG_FORCE_URL    = os.getenv('LEG_FORCE_URL',    'http://localhost:9006')

STOP_MODULE_URL = os.getenv('STOP_MODULE_URL', 'http://localhost:7001')
CARRIAGE_URL    = os.getenv('CARRIAGE_URL',    'http://localhost:7002')
TEMPERATURE_URL = os.getenv('TEMPERATURE_URL', 'http://localhost:7003')
HEATING_URL     = os.getenv('HEATING_URL',     'http://localhost:7004')
COOLING_URL     = os.getenv('COOLING_URL',     'http://localhost:7005')
TACTILE_URL     = os.getenv('TACTILE_URL',     'http://localhost:7006')

COMMS_URL        = os.getenv('COMMS_URL',        'http://localhost:6001')
MONITORING_URL   = os.getenv('MONITORING_URL',   'http://localhost:6002')
SENSORS_URL      = os.getenv('SENSORS_URL',      'http://localhost:6003')
BATTERY_CTRL_URL = os.getenv('BATTERY_CTRL_URL', 'http://localhost:6004')
CHARGER_URL      = os.getenv('CHARGER_URL',      'http://localhost:6005')
BATTERY_CELL_URL = os.getenv('BATTERY_CELL_URL', 'http://localhost:6006')

# ── Crypto ────────────────────────────────────────────────────────────────────
CRYPTO_URL = os.getenv('CRYPTO_URL', 'http://localhost:4001')

# ── Группы подсистем ──────────────────────────────────────────────────────────

ARM_SUBSYSTEMS = {
    'neural_signal_system': NEURAL_SIGNAL_URL,
    'arm_movement_system':  ARM_MOVEMENT_URL,
    'upper_arm_system':     UPPER_ARM_URL,
    'middle_arm_system':    MIDDLE_ARM_URL,
    'fingers_system':       FINGERS_URL,
    'force_control_system': FORCE_CONTROL_URL,
}

LEG_SUBSYSTEMS = {
    'leg_neural_signal_system': LEG_NEURAL_URL,
    'leg_movement_system':      LEG_MOVEMENT_URL,
    'knee_belt_system':         KNEE_BELT_URL,
    'track_system':             TRACK_SYSTEM_URL,
    'leg_force_control_system': LEG_FORCE_URL,
}

AUX_SUBSYSTEMS = {
    'stop_module':        STOP_MODULE_URL,
    'carriage_system':    CARRIAGE_URL,
    'temperature_system': TEMPERATURE_URL,
    'heating_system':     HEATING_URL,
    'cooling_system':     COOLING_URL,
    'tactile_system':     TACTILE_URL,
}

MONITORING_SUBSYSTEMS = {
    'comms_module':       COMMS_URL,
    'monitoring_system':  MONITORING_URL,
    'sensors_module':     SENSORS_URL,
    'battery_controller': BATTERY_CTRL_URL,
    'charger_module':     CHARGER_URL,
    'battery_cell':       BATTERY_CELL_URL,
}

ALL_SUBSYSTEMS = {
    **ARM_SUBSYSTEMS,
    **LEG_SUBSYSTEMS,
    **AUX_SUBSYSTEMS,
    **MONITORING_SUBSYSTEMS,
}

REQUEST_TIMEOUT = 5.0

TRUSTED_SOURCES = frozenset({
    "patient", "doctor_tablet",
    "rehab_center", "operator", "monitoring"
})

# ── База данных ───────────────────────────────────────────────────────────────

DATABASE_URL = 'sqlite:///control_system.db'
engine       = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()


class ControlState(str, Enum):
    STOPPED        = "stopped"
    CHECKING       = "checking"
    READY          = "ready"
    RUNNING        = "running"
    ERROR          = "error"
    EMERGENCY_STOP = "emergency_stop"


class SystemEventDB(Base):
    __tablename__ = 'system_events'
    id          = Column(Integer, primary_key=True, autoincrement=True)
    event_type  = Column(String(50))
    description = Column(Text)
    subsystem   = Column(String(50), nullable=True)
    body_part   = Column(String(20), nullable=True)
    success     = Column(Boolean, default=True)
    details     = Column(Text, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)


class CycleHistoryDB(Base):
    __tablename__ = 'cycle_history'
    id              = Column(Integer, primary_key=True, autoincrement=True)
    cycle_number    = Column(Integer)
    body_part       = Column(String(20))
    target          = Column(String(20))
    intent          = Column(String(50))
    strength        = Column(Float)
    speed_modifier  = Column(Float)
    can_execute     = Column(Boolean)
    command_sent    = Column(Boolean, default=False)
    command_success = Column(Boolean, nullable=True)
    error_message   = Column(Text, nullable=True)
    analysis_json   = Column(Text, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)


class MovementAuditDB(Base):
    __tablename__ = 'movement_audit'
    id             = Column(Integer, primary_key=True, autoincrement=True)
    body_part      = Column(String(20))
    target         = Column(String(20))
    intent         = Column(String(50))
    strength       = Column(Float)
    speed_modifier = Column(Float)
    source_module  = Column(String(50))
    result         = Column(String(50))
    created_at     = Column(DateTime, default=datetime.utcnow)


class GatewayCommandDB(Base):
    __tablename__ = 'gateway_commands'
    id             = Column(Integer, primary_key=True, autoincrement=True)
    action         = Column(String(50))
    source         = Column(String(50), nullable=True)
    correlation_id = Column(String(100), nullable=True)
    success        = Column(Boolean)
    result_json    = Column(Text, nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

# ── Глобальное состояние ──────────────────────────────────────────────────────

system_state: Dict[str, Any] = {
    'control_state':     ControlState.STOPPED,
    'arm_cycle_count':   0,
    'leg_cycle_count':   0,
    'total_cycle_count': 0,
    'subsystem_status':  {},
    'last_arm_analysis': None,
    'last_leg_analysis': None,
    'session_active':    False,
    'gateway_state':     'off',
}

# ── Pydantic модели ───────────────────────────────────────────────────────────

class ArmStartRequest(BaseModel):
    signals: Optional[Dict[str, float]] = None

class LegStartRequest(BaseModel):
    signals: Optional[Dict[str, float]] = None

class FullStartRequest(BaseModel):
    arm_signals: Optional[Dict[str, float]] = None
    leg_signals: Optional[Dict[str, float]] = None

class ClimateRequest(BaseModel):
    body_temp_c: float
    air_temp_c:  float

class CarriageRequest(BaseModel):
    drives_stopped: bool = True
    emergency:      bool = False
    source:         str  = "operator"

class TactileRequest(BaseModel):
    pattern:       str   = "contact_sole"
    intensity:     float = 0.5
    monitoring_ok: bool  = False

class BatteryChargeRequest(BaseModel):
    enable: bool = True

class AlarmSendRequest(BaseModel):
    alarms: List[str]

class DoctorCommandRequest(BaseModel):
    type:   str
    source: str            = 'operator'
    data:   Dict[str, Any] = {}

class EmergencyStopRequest(BaseModel):
    source: str = 'operator'

# ── Kafka ─────────────────────────────────────────────────────────────────────

bus = EventBus(client_id=MODULE_NAME)


def _on_command_message(payload: Dict[str, Any]):
    command = str(payload.get('command', '')).lower()
    logger.info("Kafka command received: %s", command)
    try:
        if command in ('start_arms', 'arm_cycle'):
            run_cycle(
                'arms', NEURAL_SIGNAL_URL, ARM_MOVEMENT_URL,
                payload.get('data', {}).get('signals'),
                ARM_SUBSYSTEMS, 'target_arm',
            )
        elif command in ('start_legs', 'leg_cycle'):
            run_cycle(
                'legs', LEG_NEURAL_URL, LEG_MOVEMENT_URL,
                payload.get('data', {}).get('signals'),
                LEG_SUBSYSTEMS, 'target_leg',
            )
        elif command in ('start_full', 'full_cycle'):
            data = payload.get('data', {})
            for part, neural, movement, subs, key in (
                ('arms', NEURAL_SIGNAL_URL, ARM_MOVEMENT_URL,
                 ARM_SUBSYSTEMS, 'target_arm'),
                ('legs', LEG_NEURAL_URL, LEG_MOVEMENT_URL,
                 LEG_SUBSYSTEMS, 'target_leg'),
            ):
                try:
                    run_cycle(part, neural, movement,
                              data.get(f'{part[:-1]}_signals'), subs, key)
                except Exception as e:
                    logger.error("%s cycle failed: %s", part, e)
        else:
            log_event('kafka_command_unknown',
                      f'Unknown command: {command}', success=False)
    except Exception as e:
        logger.exception("Kafka command handling failed: %s", e)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    bus.subscribe(
        TOPIC_COMMANDS,
        handler=_on_command_message,
        group_id='control-commands',
    )
    logger.info(
        "[system_startup] control_system v4.2 starting on port %d", PORT
    )
    log_event('system_startup',
              f'{MODULE_NAME} v4.2 starting on port {PORT}')
    yield
    bus.close()


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Exoskeleton Control System",
    version="4.2",
    lifespan=lifespan,
)

# ── Вспомогательные функции ───────────────────────────────────────────────────

def get_client() -> httpx.Client:
    return httpx.Client(timeout=REQUEST_TIMEOUT)


def log_event(
    event_type:  str,
    description: str,
    subsystem:   str  = None,
    body_part:   str  = None,
    success:     bool = True,
    details:     str  = None,
):
    level = logging.INFO if success else logging.ERROR
    logger.log(level, "[%s] %s", event_type, description)
    session = SessionLocal()
    try:
        session.add(SystemEventDB(
            event_type=event_type, description=description,
            subsystem=subsystem,   body_part=body_part,
            success=success,       details=details,
        ))
        session.commit()
    finally:
        session.close()


def save_gateway_command(
    action: str, source: str,
    correlation_id: str, success: bool, result: dict,
):
    session = SessionLocal()
    try:
        session.add(GatewayCommandDB(
            action=action, source=source,
            correlation_id=correlation_id,
            success=success,
            result_json=json.dumps(result, default=str),
        ))
        session.commit()
    finally:
        session.close()


# ── Шифрование через crypto_module ────────────────────────────────────────────

def encrypt_payload(
    data: dict,
    target: str = 'comms_module',
) -> Optional[Dict[str, str]]:
    """
    Шифрует данные через crypto_module перед отправкой в comms_module.
    Возвращает {'ciphertext': ..., 'signature': ..., 'timestamp': ...}
    или None при ошибке.
    """
    try:
        plaintext = json.dumps(data, default=str)
        with get_client() as c:
            resp = c.post(f'{CRYPTO_URL}/encrypt', json={
                'plaintext': plaintext,
                'source':    MODULE_NAME,
                'target':    target,
            })
            if resp.status_code == 200:
                result = resp.json()
                logger.debug(
                    "Payload encrypted for %s: sig=%s",
                    target, result.get('signature', '')[:8]
                )
                return result
            else:
                logger.error(
                    "Crypto module returned HTTP %d", resp.status_code
                )
                return None
    except Exception as e:
        logger.error("Encryption failed: %s", e)
        return None


def send_encrypted_alarm(alarms: list) -> dict:
    """
    Шифрует данные аларма и отправляет в comms_module уже зашифрованными.
    Цепочка: control_system → crypto_module → comms_module.
    """
    payload = {
        'alarms':    alarms,
        'source':    MODULE_NAME,
        'timestamp': datetime.utcnow().isoformat(),
    }

    encrypted = encrypt_payload(payload, target='comms_module')

    if encrypted is None:
        # Fallback: отправляем без шифрования с пометкой
        logger.warning(
            "Crypto unavailable, sending alarm without encryption"
        )
        try:
            with get_client() as c:
                resp = c.post(
                    f'{COMMS_URL}/alarm',
                    json={'alarms': alarms}
                )
                return resp.json()
        except Exception as e:
            return {'error': str(e), 'encrypted': False}

    # Отправляем зашифрованный пакет в comms_module
    try:
        with get_client() as c:
            resp = c.post(
                f'{COMMS_URL}/alarm_encrypted',
                json={
                    'ciphertext': encrypted['ciphertext'],
                    'signature':  encrypted['signature'],
                    'source':     MODULE_NAME,
                    'target':     'comms_module',
                    'alarms_count': len(alarms),
                }
            )
            result = resp.json()
            result['encrypted'] = True
            return result
    except Exception as e:
        logger.error("Encrypted alarm send failed: %s", e)
        return {'error': str(e), 'encrypted': True}


def send_encrypted_telemetry(telemetry: dict) -> dict:
    """
    Шифрует телеметрию (физиологические данные) и отправляет в comms_module.
    Цепочка: control_system → crypto_module → comms_module.
    """
    encrypted = encrypt_payload(telemetry, target='comms_module')

    if encrypted is None:
        logger.warning(
            "Crypto unavailable, telemetry not sent to comms"
        )
        return {'error': 'crypto_unavailable', 'encrypted': False}

    try:
        with get_client() as c:
            resp = c.post(
                f'{COMMS_URL}/telemetry_encrypted',
                json={
                    'ciphertext': encrypted['ciphertext'],
                    'signature':  encrypted['signature'],
                    'source':     MODULE_NAME,
                    'target':     'comms_module',
                    'timestamp':  encrypted.get('timestamp'),
                }
            )
            result = resp.json()
            result['encrypted'] = True
            return result
    except Exception as e:
        logger.error("Encrypted telemetry send failed: %s", e)
        return {'error': str(e), 'encrypted': True}


def send_encrypted_doctor_command(cmd_type: str, data: dict) -> dict:
    """
    Шифрует команду врача и отправляет в comms_module.
    """
    payload = {
        'type':      cmd_type,
        'data':      data,
        'source':    MODULE_NAME,
        'timestamp': datetime.utcnow().isoformat(),
    }

    encrypted = encrypt_payload(payload, target='comms_module')

    if encrypted is None:
        logger.warning("Crypto unavailable for doctor command")
        try:
            with get_client() as c:
                resp = c.post(
                    f'{COMMS_URL}/command',
                    json={'type': cmd_type, 'source': MODULE_NAME,
                          'data': data}
                )
                return resp.json()
        except Exception as e:
            return {'error': str(e), 'encrypted': False}

    try:
        with get_client() as c:
            resp = c.post(
                f'{COMMS_URL}/command_encrypted',
                json={
                    'ciphertext': encrypted['ciphertext'],
                    'signature':  encrypted['signature'],
                    'source':     MODULE_NAME,
                    'target':     'comms_module',
                }
            )
            result = resp.json()
            result['encrypted'] = True
            return result
    except Exception as e:
        logger.error("Encrypted command send failed: %s", e)
        return {'error': str(e), 'encrypted': True}


# ── Остальные вспомогательные функции ────────────────────────────────────────

def check_subsystem_health(name: str, url: str) -> Dict[str, Any]:
    try:
        with get_client() as c:
            resp = c.get(f'{url}/health')
            if resp.status_code == 200:
                return {'name': name, 'url': url,
                        'status': 'healthy', 'response': resp.json()}
            return {'name': name, 'url': url,
                    'status': 'unhealthy',
                    'error': f'HTTP {resp.status_code}'}
    except Exception as e:
        return {'name': name, 'url': url,
                'status': 'unreachable', 'error': str(e)}


def check_subsystems(subsystems: Dict[str, str]) -> Dict[str, Any]:
    results     = {}
    all_healthy = True
    for name, url in subsystems.items():
        result = check_subsystem_health(name, url)
        results[name] = result
        if result['status'] != 'healthy':
            all_healthy = False
            log_event('health_check_fail',
                      f"{name} is {result['status']}",
                      subsystem=name, success=False,
                      details=result.get('error'))
    return {'all_healthy': all_healthy, 'subsystems': results}


def get_stop_snapshot() -> Dict[str, Any]:
    try:
        with get_client() as c:
            resp = c.get(f'{STOP_MODULE_URL}/status')
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error("Stop module unreachable: %s", e)
        return {'drives_enabled': False, 'stopped': True, 'error': str(e)}


def aggregate_aux_telemetry() -> Dict[str, Any]:
    parts    = {}
    services = {
        'stop':        STOP_MODULE_URL,
        'carriage':    CARRIAGE_URL,
        'temperature': TEMPERATURE_URL,
        'heating':     HEATING_URL,
        'cooling':     COOLING_URL,
        'tactile':     TACTILE_URL,
    }
    with get_client() as c:
        for key, url in services.items():
            try:
                resp       = c.get(f'{url}/status')
                parts[key] = (
                    resp.json() if resp.status_code == 200
                    else {'error': f'HTTP {resp.status_code}'}
                )
            except Exception as e:
                parts[key] = {'error': str(e)}
    return parts


def aggregate_monitoring_telemetry() -> Dict[str, Any]:
    parts    = {}
    services = {
        'comms':              COMMS_URL,
        'monitoring':         MONITORING_URL,
        'sensors':            SENSORS_URL,
        'battery_controller': BATTERY_CTRL_URL,
        'charger':            CHARGER_URL,
        'battery_cell':       BATTERY_CELL_URL,
    }
    with get_client() as c:
        for key, url in services.items():
            try:
                endpoint   = '/readings' if key == 'sensors' else '/status'
                resp       = c.get(f'{url}{endpoint}')
                parts[key] = (
                    resp.json() if resp.status_code == 200
                    else {'error': f'HTTP {resp.status_code}'}
                )
            except Exception as e:
                parts[key] = {'error': str(e)}
    return parts


def apply_climate(body_temp_c: float, air_temp_c: float) -> str:
    with get_client() as c:
        c.post(f'{TEMPERATURE_URL}/sensors',
               json={'body_temp_c': body_temp_c, 'air_temp_c': air_temp_c})
        resp = c.post(f'{TEMPERATURE_URL}/decide')
        resp.raise_for_status()
        mode = resp.json()['climate_mode']
        if mode == 'heating':
            c.post(f'{COOLING_URL}/off')
            c.post(f'{HEATING_URL}/level', json={'level': 0.55})
        elif mode == 'cooling':
            c.post(f'{HEATING_URL}/off')
            c.post(f'{COOLING_URL}/speed', json={'speed': 0.65})
        else:
            c.post(f'{HEATING_URL}/off')
            c.post(f'{COOLING_URL}/off')
    return mode


def run_cycle(
    body_part:    str,
    neural_url:   str,
    movement_url: str,
    signals:      Optional[Dict[str, float]],
    subsystems:   Dict[str, str],
    target_key:   str,
) -> Dict[str, Any]:

    check         = check_subsystems(subsystems)
    neural_name   = list(subsystems.keys())[0]
    movement_name = list(subsystems.keys())[1]

    neural_ok   = (
        check['subsystems'].get(neural_name, {}).get('status') == 'healthy'
    )
    movement_ok = (
        check['subsystems'].get(movement_name, {}).get('status') == 'healthy'
    )

    if not neural_ok:
        raise HTTPException(
            status_code=503,
            detail=f'{neural_name} is not available',
        )

    if body_part == 'arms':
        system_state['arm_cycle_count'] += 1
        cycle_num = system_state['arm_cycle_count']
    else:
        system_state['leg_cycle_count'] += 1
        cycle_num = system_state['leg_cycle_count']
    system_state['total_cycle_count'] += 1
    system_state['control_state'] = ControlState.RUNNING

    log_event('cycle_start', f'{body_part} cycle {cycle_num}',
              body_part=body_part)

    try:
        payload = {'signals': signals} if signals else {}
        with get_client() as c:
            resp = c.post(f'{neural_url}/analyze', json=payload)
            if resp.status_code != 200:
                raise Exception(f'HTTP {resp.status_code}')
            analysis = resp.json()
    except Exception as e:
        system_state['control_state'] = ControlState.ERROR
        error_msg = f'Neural analysis failed: {e}'
        log_event('analysis_failed', error_msg, success=False)
        _save_cycle(cycle_num, body_part, 'none', 'idle',
                    0, 0, False, False, None, error_msg, None)
        raise HTTPException(status_code=500, detail=error_msg)

    if body_part == 'arms':
        system_state['last_arm_analysis'] = analysis
    else:
        system_state['last_leg_analysis'] = analysis

    target      = analysis.get(target_key, 'none')
    intent      = analysis.get('intent',         'idle')
    strength    = analysis.get('strength',       0)
    speed_mod   = analysis.get('speed_modifier', 0)
    can_execute = analysis.get('can_execute',    False)

    command_sent    = False
    command_success = None
    command_error   = None

    if can_execute and movement_ok:
        command = (
            {'arm': target, 'intent': intent,
             'strength': strength, 'speed_modifier': speed_mod}
            if body_part == 'arms'
            else
            {'leg': target, 'intent': intent,
             'strength': strength, 'speed_modifier': speed_mod}
        )
        try:
            with get_client() as c:
                resp            = c.post(f'{movement_url}/execute', json=command)
                command_sent    = True
                command_success = resp.status_code in [200, 204]
                if command_success:
                    log_event('command_sent',
                              f"{body_part}: {target} - {intent}",
                              body_part=body_part)
                else:
                    command_error = f'HTTP {resp.status_code}'
        except Exception as e:
            command_sent    = True
            command_success = False
            command_error   = str(e)
            log_event('command_failed', str(e),
                      body_part=body_part, success=False)
    elif not can_execute:
        log_event('no_action', f'{body_part}: idle', body_part=body_part)
    else:
        command_error = f'{movement_name} unavailable'
        log_event('command_skipped', command_error,
                  body_part=body_part, success=False)

    _save_cycle(cycle_num, body_part, target, intent,
                strength, speed_mod, can_execute,
                command_sent, command_success, command_error,
                json.dumps(analysis, default=str))

    if command_sent:
        _save_audit(body_part, target, intent, strength, speed_mod,
                    'success' if command_success else 'failed')

    system_state['control_state'] = ControlState.READY

    return {
        'cycle':           cycle_num,
        'body_part':       body_part,
        'control_state':   system_state['control_state'].value,
        'systems_healthy': check['all_healthy'],
        'analysis':        analysis,
        'command_sent':    command_sent,
        'command_success': command_success,
        'error':           command_error,
    }


def _save_cycle(
    cycle_num, body_part, target, intent,
    strength, speed_mod, can_execute,
    command_sent, command_success, error, analysis_json,
):
    session = SessionLocal()
    try:
        session.add(CycleHistoryDB(
            cycle_number=cycle_num, body_part=body_part,
            target=target,          intent=intent,
            strength=strength,      speed_modifier=speed_mod,
            can_execute=can_execute, command_sent=command_sent,
            command_success=command_success,
            error_message=error,    analysis_json=analysis_json,
        ))
        session.commit()
    finally:
        session.close()


def _save_audit(
    body_part, target, intent, strength, speed_mod, result,
):
    session = SessionLocal()
    try:
        session.add(MovementAuditDB(
            body_part=body_part, target=target,
            intent=intent,       strength=strength,
            speed_modifier=speed_mod,
            source_module='control_system', result=result,
        ))
        session.commit()
    finally:
        session.close()


# ── Эндпоинты: Health / State ─────────────────────────────────────────────────

@app.get('/health')
def health_check():
    return {
        'status':         'healthy',
        'module':         MODULE_NAME,
        'control_state':  system_state['control_state'].value,
        'session_active': system_state['session_active'],
        'gateway_state':  system_state['gateway_state'],
        'arm_cycles':     system_state['arm_cycle_count'],
        'leg_cycles':     system_state['leg_cycle_count'],
        'total_cycles':   system_state['total_cycle_count'],
    }


@app.get('/state')
def get_state():
    return {
        'control_state':     system_state['control_state'].value,
        'session_active':    system_state['session_active'],
        'gateway_state':     system_state['gateway_state'],
        'arm_cycles':        system_state['arm_cycle_count'],
        'leg_cycles':        system_state['leg_cycle_count'],
        'total_cycles':      system_state['total_cycle_count'],
        'last_arm_analysis': system_state['last_arm_analysis'],
        'last_leg_analysis': system_state['last_leg_analysis'],
    }


# ── Эндпоинты: Проверка систем ────────────────────────────────────────────────

@app.get('/check_systems')
def check_all_systems():
    system_state['control_state'] = ControlState.CHECKING
    result = check_subsystems(ALL_SUBSYSTEMS)
    system_state['subsystem_status'] = result['subsystems']
    if result['all_healthy']:
        system_state['control_state'] = ControlState.READY
        log_event('systems_check', 'All systems healthy')
    else:
        system_state['control_state'] = ControlState.ERROR
        unhealthy = [n for n, i in result['subsystems'].items()
                     if i['status'] != 'healthy']
        log_event('systems_check', f'Unhealthy: {unhealthy}',
                  success=False)
    result['control_state'] = system_state['control_state'].value
    return result


@app.get('/check_arms')
def check_arm_systems():
    result = check_subsystems(ARM_SUBSYSTEMS)
    result['group'] = 'arms'
    return result


@app.get('/check_legs')
def check_leg_systems():
    result = check_subsystems(LEG_SUBSYSTEMS)
    result['group'] = 'legs'
    return result


@app.get('/check_aux')
def check_aux_systems():
    result = check_subsystems(AUX_SUBSYSTEMS)
    result['group'] = 'auxiliary'
    return result


@app.get('/check_monitoring')
def check_monitoring_systems():
    result = check_subsystems(MONITORING_SUBSYSTEMS)
    result['group'] = 'monitoring'
    return result


# ── Эндпоинты: Управление руками/ногами ──────────────────────────────────────

@app.post('/start/arms')
def start_arm_cycle(body: ArmStartRequest = ArmStartRequest()):
    return run_cycle('arms', NEURAL_SIGNAL_URL, ARM_MOVEMENT_URL,
                     body.signals, ARM_SUBSYSTEMS, 'target_arm')


@app.post('/start/legs')
def start_leg_cycle(body: LegStartRequest = LegStartRequest()):
    return run_cycle('legs', LEG_NEURAL_URL, LEG_MOVEMENT_URL,
                     body.signals, LEG_SUBSYSTEMS, 'target_leg')


@app.post('/start/full')
def start_full_cycle(body: FullStartRequest = FullStartRequest()):
    arm_result = arm_error = leg_result = leg_error = None
    try:
        arm_result = run_cycle('arms', NEURAL_SIGNAL_URL, ARM_MOVEMENT_URL,
                               body.arm_signals, ARM_SUBSYSTEMS, 'target_arm')
    except HTTPException as e:
        arm_error = e.detail

    try:
        leg_result = run_cycle('legs', LEG_NEURAL_URL, LEG_MOVEMENT_URL,
                               body.leg_signals, LEG_SUBSYSTEMS, 'target_leg')
    except HTTPException as e:
        leg_error = e.detail

    return {
        'arms':          arm_result or {'error': arm_error},
        'legs':          leg_result or {'error': leg_error},
        'control_state': system_state['control_state'].value,
    }


# ── Эндпоинты: Остановка ─────────────────────────────────────────────────────

@app.post('/stop/emergency')
def full_emergency_stop(
    body: EmergencyStopRequest = EmergencyStopRequest(),
):
    system_state['control_state']  = ControlState.EMERGENCY_STOP
    system_state['session_active'] = False
    system_state['gateway_state']  = 'emergency'
    logger.critical("!!! EMERGENCY STOP from %s !!!", body.source)
    log_event('emergency_stop', f'Emergency stop from {body.source}',
              body_part='all')

    results = {}
    reason  = (
        'patient_emergency'    if body.source == 'patient'
        else 'monitoring_obstacle' if body.source == 'monitoring'
        else 'doctor_emergency'
    )
    try:
        with get_client() as c:
            resp = c.post(f'{STOP_MODULE_URL}/emergency-stop',
                          json={'reason': reason})
            results['stop_module'] = {'success': resp.status_code == 200}
    except Exception as e:
        results['stop_module'] = {'success': False, 'error': str(e)}

    for name, url in {**ARM_SUBSYSTEMS, **LEG_SUBSYSTEMS}.items():
        try:
            with get_client() as c:
                resp          = c.post(f'{url}/emergency_stop')
                results[name] = {'success': resp.status_code == 200}
        except Exception as e:
            results[name] = {'success': False, 'error': str(e)}

    return {'message': 'Emergency stop activated',
            'source': body.source, 'results': results}


@app.post('/stop/emergency/arms')
def emergency_stop_arms():
    log_event('emergency_stop_arms', 'Arms emergency stop')
    results = {}
    for name, url in ARM_SUBSYSTEMS.items():
        try:
            with get_client() as c:
                resp          = c.post(f'{url}/emergency_stop')
                results[name] = {'success': resp.status_code == 200}
        except Exception as e:
            results[name] = {'success': False, 'error': str(e)}
    return {'message': 'Arms emergency stop', 'results': results}


@app.post('/stop/emergency/legs')
def emergency_stop_legs():
    log_event('emergency_stop_legs', 'Legs emergency stop')
    results = {}
    for name, url in LEG_SUBSYSTEMS.items():
        try:
            with get_client() as c:
                resp          = c.post(f'{url}/emergency_stop')
                results[name] = {'success': resp.status_code == 200}
        except Exception as e:
            results[name] = {'success': False, 'error': str(e)}
    return {'message': 'Legs emergency stop', 'results': results}


@app.post('/stop/smooth')
def smooth_stop():
    log_event('smooth_stop', 'Smooth stop executed')
    try:
        with get_client() as c:
            resp = c.post(f'{STOP_MODULE_URL}/smooth-stop')
            return {'ok': True, 'result': resp.json()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post('/stop/reset')
def reset_emergency(source: str = 'operator'):
    authorized = {'doctor_tablet', 'rehab_center', 'operator'}
    if source not in authorized:
        raise HTTPException(status_code=403,
                            detail=f'Source {source} not authorized')
    try:
        with get_client() as c:
            resp = c.post(f'{STOP_MODULE_URL}/reset-emergency',
                          json={'authorized': True})
            ok   = resp.json().get('ok', False)
            if ok:
                system_state['gateway_state']  = 'stopped'
                system_state['control_state']  = ControlState.STOPPED
                log_event('emergency_reset', f'Reset by {source}')
            return {'ok': ok, 'result': resp.json()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get('/stop/status')
def get_stop_status():
    try:
        return get_stop_snapshot()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Эндпоинты: Коляска ───────────────────────────────────────────────────────

@app.post('/carriage/open')
def open_carriage(body: CarriageRequest):
    if body.source not in TRUSTED_SOURCES and not body.emergency:
        raise HTTPException(status_code=403, detail='Untrusted source')
    try:
        snap           = get_stop_snapshot()
        drives_stopped = not snap.get('drives_enabled', False)
        with get_client() as c:
            resp   = c.post(f'{CARRIAGE_URL}/open',
                            json={'drives_stopped': drives_stopped,
                                  'emergency': body.emergency})
            result = resp.json()
            log_event('carriage_open',
                      f"Source: {body.source}, ok={result.get('ok')}")
            return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post('/carriage/close')
def close_carriage(source: str = 'operator'):
    if source not in TRUSTED_SOURCES:
        raise HTTPException(status_code=403, detail='Untrusted source')
    try:
        with get_client() as c:
            resp   = c.post(f'{CARRIAGE_URL}/close')
            result = resp.json()
            log_event('carriage_close', f"Source: {source}")
            return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get('/carriage/status')
def carriage_status():
    try:
        with get_client() as c:
            return c.get(f'{CARRIAGE_URL}/status').json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Эндпоинты: Климат ────────────────────────────────────────────────────────

@app.post('/climate/update')
def update_climate(body: ClimateRequest):
    try:
        mode = apply_climate(body.body_temp_c, body.air_temp_c)
        log_event('climate_update',
                  f"Mode: {mode}, body={body.body_temp_c}, "
                  f"air={body.air_temp_c}")
        return {'ok': True, 'climate_mode': mode,
                'temperatures': {'body_temp_c': body.body_temp_c,
                                 'air_temp_c': body.air_temp_c}}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get('/climate/status')
def climate_status():
    result = {}
    for name, url in {'temperature': TEMPERATURE_URL,
                      'heating': HEATING_URL,
                      'cooling': COOLING_URL}.items():
        try:
            with get_client() as c:
                result[name] = c.get(f'{url}/status').json()
        except Exception as e:
            result[name] = {'error': str(e)}
    return result


# ── Эндпоинты: Тактильная обратная связь ─────────────────────────────────────

@app.post('/tactile/emit')
def tactile_emit(body: TactileRequest):
    try:
        snap           = get_stop_snapshot()
        source_trusted = (
            body.monitoring_ok
            and system_state['session_active']
            and not snap.get('stopped', False)
        )
        with get_client() as c:
            return c.post(f'{TACTILE_URL}/emit',
                          json={'pattern':       body.pattern,
                                'intensity':     body.intensity,
                                'source_trusted': source_trusted}).json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post('/tactile/warning')
def send_tactile_warning():
    try:
        with get_client() as c:
            resp = c.post(f'{TACTILE_URL}/emit',
                          json={'pattern': 'warning', 'intensity': 0.7,
                                'source_trusted': True})
            log_event('tactile_warning', 'Warning signal sent')
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get('/tactile/status')
def tactile_status():
    try:
        with get_client() as c:
            return c.get(f'{TACTILE_URL}/status').json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Эндпоинты: Мониторинг ────────────────────────────────────────────────────

@app.get('/monitoring/telemetry')
def get_monitoring_telemetry():
    try:
        with get_client() as c:
            return c.get(f'{MONITORING_URL}/telemetry').json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get('/monitoring/sensors')
def get_sensor_readings():
    try:
        with get_client() as c:
            return c.get(f'{SENSORS_URL}/readings').json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get('/monitoring/battery')
def get_battery_status():
    try:
        with get_client() as c:
            return c.get(f'{BATTERY_CTRL_URL}/status').json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post('/monitoring/battery/charge')
def control_battery_charge(body: BatteryChargeRequest):
    try:
        with get_client() as c:
            resp = c.post(f'{BATTERY_CTRL_URL}/control/charge',
                          json={'enable': body.enable})
            log_event('battery_charge',
                      f"Charge {'enabled' if body.enable else 'disabled'}")
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Эндпоинты: Связь с врачами (зашифрованная) ───────────────────────────────

@app.get('/comms/status')
def get_comms_status():
    try:
        with get_client() as c:
            return c.get(f'{COMMS_URL}/status').json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post('/comms/alarm')
def send_alarm(body: AlarmSendRequest):
    """
    Отправляет аларм в comms_module в зашифрованном виде.
    Цепочка: control_system → crypto_module → comms_module.
    """
    result = send_encrypted_alarm(body.alarms)
    log_event('alarm_sent',
              f"Alarms (encrypted): {body.alarms}",
              details=json.dumps(result))
    return result


@app.post('/comms/command')
def send_doctor_command(body: DoctorCommandRequest):
    """
    Отправляет команду врача через зашифрованный канал.
    Цепочка: control_system → crypto_module → comms_module.
    """
    result = send_encrypted_doctor_command(body.type, body.data)
    log_event('doctor_command',
              f"Command (encrypted): {body.type} from {body.source}")
    return result


@app.post('/comms/telemetry')
def send_telemetry_to_comms(telemetry: Dict[str, Any] = Body(...)):
    """
    Отправляет физиологическую телеметрию в зашифрованном виде.
    Цепочка: control_system → crypto_module → comms_module.
    """
    result = send_encrypted_telemetry(telemetry)
    log_event('telemetry_sent_encrypted',
              'Telemetry encrypted and sent to comms')
    return result


@app.get('/comms/history')
def get_comms_history():
    try:
        with get_client() as c:
            return c.get(f'{COMMS_URL}/comms_history').json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Эндпоинты: Шлюз команд ───────────────────────────────────────────────────

@app.post('/commands')
def gateway_commands(body: Dict[str, Any] = Body(...)) -> JSONResponse:
    cid    = body.get('correlation_id')
    action = body.get('action')

    if not action:
        return JSONResponse(
            {'ok': False, 'error': 'missing_action',
             'correlation_id': cid}, 422
        )

    logger.info("Gateway: action=%s source=%s cid=%s",
                action, body.get('source'), cid)

    try:
        result                   = _gateway_dispatch(action, body)
        ok                       = result.get('ok', True)
        result['correlation_id'] = cid
        save_gateway_command(
            action, str(body.get('source', '')), str(cid), ok, result
        )
        return JSONResponse(result, status_code=200 if ok else 422)
    except httpx.HTTPError as e:
        err = {'ok': False, 'correlation_id': cid,
               'error': f'upstream:{e!s}'}
        save_gateway_command(
            action, str(body.get('source', '')), str(cid), False, err
        )
        return JSONResponse(err, status_code=502)


def _gateway_dispatch(action: str, body: Dict[str, Any]) -> Dict[str, Any]:
    src = str(body.get('source', ''))

    if action == 'initialize':
        with get_client() as c:
            c.post(f'{STOP_MODULE_URL}/smooth-stop')
            c.post(f'{HEATING_URL}/off')
            c.post(f'{COOLING_URL}/off')
        system_state['session_active'] = False
        system_state['gateway_state']  = 'ready'
        log_event('gateway_initialize', 'System initialized')
        return {'ok': True, 'result': {'initialized': True},
                'telemetry': aggregate_aux_telemetry()}

    if action == 'start_session':
        if src not in TRUSTED_SOURCES:
            with get_client() as c:
                c.post(f'{STOP_MODULE_URL}/emergency-stop',
                       json={'reason': 'unauthorized_command'})
            system_state['gateway_state'] = 'emergency'
            return {'ok': False, 'error': 'untrusted_source',
                    'telemetry': aggregate_aux_telemetry()}
        snap = get_stop_snapshot()
        if snap.get('stopped'):
            return {'ok': False, 'error': 'system_stopped',
                    'telemetry': aggregate_aux_telemetry()}
        with get_client() as c:
            c.post(f'{STOP_MODULE_URL}/allow-movement')
        system_state['session_active'] = True
        system_state['gateway_state']  = 'session_active'
        log_event('session_start', f'Session started by {src}')
        return {'ok': True, 'telemetry': aggregate_aux_telemetry()}

    if action == 'end_session':
        if src not in TRUSTED_SOURCES:
            return {'ok': False, 'error': 'untrusted_source',
                    'telemetry': aggregate_aux_telemetry()}
        with get_client() as c:
            c.post(f'{STOP_MODULE_URL}/smooth-stop')
        system_state['session_active'] = False
        system_state['gateway_state']  = 'ready'
        log_event('session_end', f'Session ended by {src}')
        return {'ok': True, 'telemetry': aggregate_aux_telemetry()}

    if action == 'emergency_stop':
        reason = (
            'patient_emergency'    if src == 'patient'
            else 'monitoring_obstacle' if src == 'monitoring'
            else 'doctor_emergency'
        )
        with get_client() as c:
            c.post(f'{STOP_MODULE_URL}/emergency-stop',
                   json={'reason': reason})
        system_state['session_active'] = False
        system_state['gateway_state']  = 'emergency'
        system_state['control_state']  = ControlState.EMERGENCY_STOP
        log_event('gateway_estop', f'From {src}')
        return {'ok': True,
                'event': {'type': 'emergency_stop', 'source': src},
                'telemetry': aggregate_aux_telemetry()}

    if action == 'reset_emergency':
        if src not in ('doctor_tablet', 'rehab_center', 'operator'):
            return {'ok': False, 'error': 'forbidden_source',
                    'telemetry': aggregate_aux_telemetry()}
        with get_client() as c:
            resp = c.post(f'{STOP_MODULE_URL}/reset-emergency',
                          json={'authorized': True})
        ok = resp.json().get('ok', False)
        if ok:
            system_state['gateway_state'] = 'stopped'
        return {'ok': ok, 'telemetry': aggregate_aux_telemetry()}

    if action == 'open_carriage':
        if src not in TRUSTED_SOURCES and not body.get('emergency'):
            return {'ok': False, 'error': 'untrusted_source',
                    'telemetry': aggregate_aux_telemetry()}
        snap           = get_stop_snapshot()
        drives_stopped = not snap.get('drives_enabled', False)
        with get_client() as c:
            resp = c.post(f'{CARRIAGE_URL}/open',
                          json={'drives_stopped': drives_stopped,
                                'emergency': bool(body.get('emergency', False))})
        log_event('carriage_open', f'Via gateway from {src}')
        return {'ok': resp.json().get('ok', False),
                'telemetry': aggregate_aux_telemetry()}

    if action == 'close_carriage':
        if src not in TRUSTED_SOURCES:
            return {'ok': False, 'error': 'untrusted_source',
                    'telemetry': aggregate_aux_telemetry()}
        with get_client() as c:
            resp = c.post(f'{CARRIAGE_URL}/close')
        log_event('carriage_close', f'Via gateway from {src}')
        return {'ok': resp.json().get('ok', False),
                'telemetry': aggregate_aux_telemetry()}

    if action == 'update_climate':
        mode = apply_climate(float(body['body_temp_c']),
                             float(body['air_temp_c']))
        log_event('climate_update', f'Mode: {mode}')
        return {'ok': True, 'result': {'climate_mode': mode},
                'telemetry': aggregate_aux_telemetry()}

    if action == 'tactile_contact':
        snap    = get_stop_snapshot()
        trusted = (
            bool(body.get('monitoring_ok', False))
            and system_state['session_active']
            and not snap.get('stopped', False)
        )
        with get_client() as c:
            resp = c.post(f'{TACTILE_URL}/emit',
                          json={'pattern':       'contact_sole',
                                'intensity':     float(body.get('intensity', 0.5)),
                                'source_trusted': trusted})
        return {'ok': True,
                'result': {'tactile': resp.json().get('message')},
                'telemetry': aggregate_aux_telemetry()}

    if action in ('telemetry', 'snapshot'):
        return {'ok':         True,
                'telemetry':  aggregate_aux_telemetry(),
                'monitoring': aggregate_monitoring_telemetry()}

    if action == 'send_alarm':
        # Зашифрованная отправка аларма через gateway
        alarms = body.get('alarms', [])
        result = send_encrypted_alarm(alarms)
        log_event('gateway_alarm_encrypted', f'Alarms: {alarms}')
        return {'ok': True, 'result': result,
                'telemetry': aggregate_aux_telemetry()}

    if action == 'send_telemetry':
        # Зашифрованная отправка телеметрии через gateway
        telemetry_data = body.get('telemetry', {})
        result = send_encrypted_telemetry(telemetry_data)
        log_event('gateway_telemetry_encrypted', 'Telemetry sent encrypted')
        return {'ok': True, 'result': result,
                'telemetry': aggregate_aux_telemetry()}

    return {'ok':    False,
            'error': f'unknown_action:{action}',
            'telemetry': aggregate_aux_telemetry()}


# ── Эндпоинты: Сброс ─────────────────────────────────────────────────────────

@app.post('/reset')
def reset_system():
    results = {}
    for name, url in ALL_SUBSYSTEMS.items():
        try:
            with get_client() as c:
                resp          = c.post(f'{url}/reset')
                results[name] = {'success': resp.status_code == 200}
        except Exception as e:
            results[name] = {'success': False, 'error': str(e)}

    system_state.update({
        'control_state':     ControlState.STOPPED,
        'arm_cycle_count':   0,
        'leg_cycle_count':   0,
        'total_cycle_count': 0,
        'last_arm_analysis': None,
        'last_leg_analysis': None,
        'session_active':    False,
        'gateway_state':     'off',
    })
    log_event('system_reset', 'Full reset', body_part='all')
    return {'message': 'Full reset complete', 'results': results}


# ── Эндпоинты: Телеметрия ────────────────────────────────────────────────────

@app.get('/telemetry')
def full_telemetry():
    return {
        'gateway': {
            'session_active': system_state['session_active'],
            'gateway_state':  system_state['gateway_state'],
            'control_state':  system_state['control_state'].value,
        },
        'auxiliary':  aggregate_aux_telemetry(),
        'monitoring': aggregate_monitoring_telemetry(),
    }


# ── Эндпоинты: История ───────────────────────────────────────────────────────

@app.get('/event_log')
def get_event_log(
    limit:      int           = Query(100, ge=1, le=1000),
    event_type: Optional[str] = None,
    body_part:  Optional[str] = None,
):
    session = SessionLocal()
    try:
        query = session.query(SystemEventDB)
        if event_type:
            query = query.filter(SystemEventDB.event_type == event_type)
        if body_part:
            query = query.filter(SystemEventDB.body_part == body_part)
        events = (query.order_by(SystemEventDB.created_at.desc())
                  .limit(limit).all())
        return [{
            'id':          e.id,
            'event_type':  e.event_type,
            'description': e.description,
            'subsystem':   e.subsystem,
            'body_part':   e.body_part,
            'success':     e.success,
            'details':     e.details,
            'created_at':  e.created_at.strftime('%Y-%m-%d %H:%M:%S')
                           if e.created_at else None,
        } for e in events]
    finally:
        session.close()


@app.get('/cycle_history')
def get_cycle_history(
    limit:     int           = Query(100, ge=1, le=1000),
    body_part: Optional[str] = None,
):
    session = SessionLocal()
    try:
        query = session.query(CycleHistoryDB)
        if body_part:
            query = query.filter(CycleHistoryDB.body_part == body_part)
        cycles = (query.order_by(CycleHistoryDB.created_at.desc())
                  .limit(limit).all())
        return [{
            'id':              c.id,
            'cycle_number':    c.cycle_number,
            'body_part':       c.body_part,
            'target':          c.target,
            'intent':          c.intent,
            'strength':        c.strength,
            'speed_modifier':  c.speed_modifier,
            'can_execute':     c.can_execute,
            'command_sent':    c.command_sent,
            'command_success': c.command_success,
            'error_message':   c.error_message,
            'created_at':      c.created_at.strftime('%Y-%m-%d %H:%M:%S')
                               if c.created_at else None,
        } for c in cycles]
    finally:
        session.close()


@app.get('/gateway_history')
def get_gateway_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        commands = (session.query(GatewayCommandDB)
                    .order_by(GatewayCommandDB.created_at.desc())
                    .limit(limit).all())
        return [{
            'id':             c.id,
            'action':         c.action,
            'source':         c.source,
            'correlation_id': c.correlation_id,
            'success':        c.success,
            'created_at':     c.created_at.strftime('%Y-%m-%d %H:%M:%S')
                              if c.created_at else None,
        } for c in commands]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info("Starting %s on %s:%d", MODULE_NAME, HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT)