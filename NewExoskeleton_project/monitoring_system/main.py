# monitoring_system/main.py
import os
import logging
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float, Boolean, String, DateTime, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 6002))
MODULE_NAME = os.getenv('MODULE_NAME', 'monitoring_system')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

SENSORS_URL = os.getenv('SENSORS_URL', 'http://localhost:6003')
BATTERY_CTRL_URL = os.getenv(
    'BATTERY_CTRL_URL', 'http://localhost:6004'
)
COMMS_URL = os.getenv('COMMS_URL', 'http://localhost:6001')
REQUEST_TIMEOUT = 5.0

DATABASE_URL = 'sqlite:///monitoring_system.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class TelemetryLogDB(Base):
    __tablename__ = 'telemetry_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    joint_angle = Column(Float)
    torque = Column(Float)
    motor_temp = Column(Float)
    battery_soc = Column(Float)
    alarms = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class AlarmLogDB(Base):
    __tablename__ = 'alarm_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    alarm_type = Column(String(100))
    value = Column(Float, nullable=True)
    sent_to_comms = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


def save_telemetry(data: dict, alarms: list):
    session = SessionLocal()
    try:
        session.add(TelemetryLogDB(
            joint_angle=data.get('joint_angle'),
            torque=data.get('torque'),
            motor_temp=data.get('motor_temp'),
            battery_soc=data.get('battery', {}).get('soc'),
            alarms=','.join(alarms) if alarms else None
        ))
        session.commit()
    finally:
        session.close()


def save_alarm(alarm_type: str, value: float = None, sent: bool = False):
    session = SessionLocal()
    try:
        session.add(AlarmLogDB(
            alarm_type=alarm_type,
            value=value,
            sent_to_comms=sent
        ))
        session.commit()
    finally:
        session.close()


app = FastAPI(title="Monitoring System", version="2.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/telemetry')
def get_telemetry():
    """
    Система мониторинга:
    monitoring → sensors (показания)
    monitoring → battery_controller (заряд)
    monitoring → comms (аларм если нужно)
    """
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            sensors_resp = c.get(f'{SENSORS_URL}/readings')
            battery_resp = c.get(f'{BATTERY_CTRL_URL}/status')

        sensor_data = sensors_resp.json()
        battery_data = battery_resp.json()

        telemetry = {
            **sensor_data,
            'battery': battery_data
        }

        # Проверка алармов
        alarms = []

        if sensor_data.get('joint_angle', 0) > 120:
            alarms.append('HYPEREXTENSION')
            save_alarm(
                'HYPEREXTENSION',
                value=sensor_data['joint_angle']
            )
            logger.warning(
                f"ALARM: HYPEREXTENSION "
                f"angle={sensor_data['joint_angle']}"
            )

        if battery_data.get('soc', 100) < 10:
            alarms.append('BATTERY_LOW')
            save_alarm('BATTERY_LOW', value=battery_data['soc'])
            logger.warning(
                f"ALARM: BATTERY_LOW soc={battery_data['soc']}"
            )

        if sensor_data.get('motor_temp', 0) > 70:
            alarms.append('MOTOR_OVERHEAT')
            save_alarm(
                'MOTOR_OVERHEAT',
                value=sensor_data['motor_temp']
            )
            logger.warning(
                f"ALARM: MOTOR_OVERHEAT "
                f"temp={sensor_data['motor_temp']}"
            )

        # Отправка алармов в систему связи
        if alarms:
            try:
                with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
                    c.post(
                        f'{COMMS_URL}/alarm',
                        json={'alarms': alarms}
                    )
                for alarm in alarms:
                    save_alarm(alarm, sent_to_comms=True)
                logger.info(f"Alarms sent to comms: {alarms}")
            except Exception as e:
                logger.error(f"Failed to send alarms: {e}")

        telemetry['alarms'] = alarms
        save_telemetry(telemetry, alarms)

        return telemetry

    except Exception as e:
        logger.error(f"Telemetry error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@app.post('/emergency_stop')
def emergency_stop():
    """Экстренная остановка от мониторинга"""
    logger.critical("Emergency stop triggered by monitoring")
    save_alarm('EMERGENCY_STOP_MONITORING')
    return {'ok': True, 'message': 'Emergency stop triggered'}


@app.get('/telemetry_history')
def get_telemetry_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(TelemetryLogDB)
            .order_by(TelemetryLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id,
            'joint_angle': l.joint_angle,
            'torque': l.torque,
            'motor_temp': l.motor_temp,
            'battery_soc': l.battery_soc,
            'alarms': l.alarms,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


@app.get('/alarm_history')
def get_alarm_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        alarms = (
            session.query(AlarmLogDB)
            .order_by(AlarmLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': a.id,
            'alarm_type': a.alarm_type,
            'value': a.value,
            'sent_to_comms': a.sent_to_comms,
            'created_at': a.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if a.created_at else None
        } for a in alarms]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)