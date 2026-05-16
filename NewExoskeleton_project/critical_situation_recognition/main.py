import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, Boolean, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

from kafka_bus import (
    EventBus, TOPIC_EMERGENCY, TOPIC_SENSORS_VERIFIED
)

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5102))
MODULE_NAME = os.getenv('MODULE_NAME', 'critical_situation_recognition')

THRESHOLDS = {
    'joint_angle': (0, 150),
    'joint_angular_velocity': (-150, 150),
    'torque': (0, 80),
    'motor_temp': (0, 70),
    'imu_acceleration': (-20, 20),
    'balance_deviation': (-15, 15),
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = os.getenv(
    'DATABASE_URL', 'sqlite:///critical_situation.db'
)
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class SituationAlertDB(Base):
    __tablename__ = 'situation_alerts'

    id = Column(Integer, primary_key=True)
    metric = Column(String(50))
    value = Column(Float)
    threshold_min = Column(Float)
    threshold_max = Column(Float)
    critical = Column(Boolean)
    transport = Column(String(20))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(engine)

bus = EventBus(client_id=MODULE_NAME)


class TelemetryInput(BaseModel):
    metric: str
    value: float
    source: str = "sensors_module"
    sensor_trusted: bool = True


class AlertResponse(BaseModel):
    critical: bool
    action: str
    metric: str
    value: float
    reason: str


def save_alert(metric, value, t_min, t_max, critical, transport):
    session = SessionLocal()
    try:
        session.add(SituationAlertDB(
            metric=metric,
            value=value,
            threshold_min=t_min,
            threshold_max=t_max,
            critical=critical,
            transport=transport,
        ))
        session.commit()
    finally:
        session.close()


def evaluate_metric(metric: str, value: float, transport: str) -> dict:
    if metric not in THRESHOLDS:
        return {
            'critical': False,
            'reason': 'metric_not_configured',
        }

    t_min, t_max = THRESHOLDS[metric]
    is_critical = not (t_min <= value <= t_max)

    save_alert(metric, value, t_min, t_max, is_critical, transport)

    if is_critical:
        logger.critical(
            "CRITICAL [%s]: %s=%s out of [%s, %s]",
            transport, metric, value, t_min, t_max
        )
        bus.publish(TOPIC_EMERGENCY, {
            'source': MODULE_NAME,
            'reason': f'critical_{metric}',
            'metric': metric,
            'value': value,
        })
        return {
            'critical': True,
            'reason': 'threshold_exceeded',
        }

    return {
        'critical': False,
        'reason': 'within_limits',
    }


def _on_sensor_message(payload: dict):
    if not payload.get('trusted', True):
        return

    for metric in THRESHOLDS:
        if metric in payload:
            try:
                value = float(payload[metric])
            except (TypeError, ValueError):
                continue

            evaluate_metric(metric, value, transport='kafka')


app = FastAPI(title="Critical Situation Recognition", version="3.1")


@app.on_event('startup')
def on_startup():
    bus.subscribe(
        TOPIC_SENSORS_VERIFIED,
        handler=_on_sensor_message,
        group_id='critical-situation',
    )


@app.on_event('shutdown')
def on_shutdown():
    bus.close()


@app.get('/health')
def health():
    return {'status': 'healthy', 'module': MODULE_NAME, 'port': PORT}


@app.post('/analyze', response_model=AlertResponse)
def analyze(body: TelemetryInput):
    if not body.sensor_trusted:
        return AlertResponse(
            critical=False,
            action='ignore_untrusted',
            metric=body.metric,
            value=body.value,
            reason='sensor_not_trusted',
        )

    result = evaluate_metric(body.metric, body.value, transport='http')

    return AlertResponse(
        critical=result['critical'],
        action='emergency_published' if result['critical'] else 'continue',
        metric=body.metric,
        value=body.value,
        reason=result['reason'],
    )


@app.get('/status')
def status():
    return {
        'service': MODULE_NAME,
        'port': PORT,
        'thresholds': THRESHOLDS,
        'last_check': datetime.now(timezone.utc).isoformat(),
    }


@app.get('/history')
def history(limit: int = 100):
    session = SessionLocal()
    try:
        alerts = (
            session.query(SituationAlertDB)
            .order_by(SituationAlertDB.created_at.desc())
            .limit(limit)
            .all()
        )
        return [{
            'id': a.id,
            'metric': a.metric,
            'value': a.value,
            'critical': a.critical,
            'transport': a.transport,
            'created_at': a.created_at.isoformat(),
        } for a in alerts]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info("Starting %s on %s:%s", MODULE_NAME, HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT)