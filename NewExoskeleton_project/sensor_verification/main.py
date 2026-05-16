import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from datetime import datetime, timezone

import uvicorn
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine, Column, Integer, Float, Boolean, String, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

from kafka_bus import (
    EventBus, TOPIC_SENSORS_RAW, TOPIC_SENSORS_VERIFIED
)

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5103))
MODULE_NAME = os.getenv('MODULE_NAME', 'sensor_verification')

SENSORS_URL = os.getenv(
    'SENSORS_URL', 'http://localhost:6003/readings'
)
CRITICAL_SENSORS_URL = os.getenv(
    'CRITICAL_SENSORS_URL', 'http://localhost:4003/readings'
)
MAX_DEVIATION = float(os.getenv('MAX_DEVIATION', '5.0'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = os.getenv(
    'DATABASE_URL', 'sqlite:///sensor_verification.db'
)
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class VerificationLogDB(Base):
    __tablename__ = 'verification_log'

    id = Column(Integer, primary_key=True)
    metric = Column(String(50))
    regular_value = Column(Float)
    critical_value = Column(Float)
    deviation = Column(Float)
    passed = Column(Boolean)
    forwarded = Column(Boolean)
    transport = Column(String(20))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(engine)

bus = EventBus(client_id=MODULE_NAME)


class VerificationRequest(BaseModel):
    metric: str
    regular_value: float
    critical_value: float
    tolerance: float = Field(default=MAX_DEVIATION, ge=0)


class VerificationResponse(BaseModel):
    metric: str
    passed: bool
    deviation: float
    forwarded_value: float
    reason: str


def save_log(metric, reg, crit, dev, passed, forwarded, transport):
    session = SessionLocal()
    try:
        session.add(VerificationLogDB(
            metric=metric,
            regular_value=reg,
            critical_value=crit,
            deviation=dev,
            passed=passed,
            forwarded=forwarded,
            transport=transport,
        ))
        session.commit()
    finally:
        session.close()


def fetch_critical_snapshot() -> dict:
    try:
        with httpx.Client(timeout=2.0) as c:
            resp = c.get(CRITICAL_SENSORS_URL)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.debug("Critical sensors fetch failed: %s", e)

    return {}


def verify_metric(metric: str, regular: float, critical: float,
                  tolerance: float = MAX_DEVIATION,
                  transport: str = 'http') -> dict:
    deviation = abs(regular - critical)
    passed = deviation <= tolerance

    save_log(
        metric, regular, critical, deviation,
        passed, passed, transport,
    )

    return {
        'metric': metric,
        'passed': passed,
        'deviation': round(deviation, 3),
        'forwarded_value': critical if passed else 0.0,
        'reason': 'ok' if passed else (
            f'deviation_{deviation:.2f}_exceeds_{tolerance}'
        ),
    }


def _on_raw_sensor_message(payload: dict):
    critical = fetch_critical_snapshot()
    verified = {'trusted': True}
    any_failed = False

    for metric in ('joint_angle', 'joint_angular_velocity'):
        if metric not in payload:
            continue

        reg = float(payload[metric])
        crit = float(critical.get(metric, reg))
        result = verify_metric(metric, reg, crit, transport='kafka')

        if result['passed']:
            verified[metric] = reg
        else:
            any_failed = True
            verified[metric] = None

    for metric in ('torque', 'motor_temp', 'imu_roll',
                   'imu_pitch', 'imu_yaw'):
        if metric in payload:
            verified[metric] = payload[metric]

    if any_failed:
        verified['trusted'] = False
        logger.warning("Sensor verification failed, data marked untrusted")

    bus.publish(TOPIC_SENSORS_VERIFIED, verified)


app = FastAPI(title="Sensor Verification", version="3.1")


@app.on_event('startup')
def on_startup():
    bus.subscribe(
        TOPIC_SENSORS_RAW,
        handler=_on_raw_sensor_message,
        group_id='sensor-verification',
    )


@app.on_event('shutdown')
def on_shutdown():
    bus.close()


@app.get('/health')
def health():
    return {
        'status': 'healthy',
        'module': MODULE_NAME,
        'port': PORT,
    }


@app.post('/verify', response_model=VerificationResponse)
def verify(body: VerificationRequest):
    result = verify_metric(
        body.metric, body.regular_value, body.critical_value,
        tolerance=body.tolerance, transport='http',
    )
    logger.info(
        "Verify %s: %s | dev=%s",
        body.metric,
        'PASS' if result['passed'] else 'FAIL',
        result['deviation']
    )
    return VerificationResponse(**result)


@app.get('/auto_verify')
def auto_verify(metric: str = "joint_angle"):
    try:
        with httpx.Client(timeout=2.0) as c:
            reg_resp = c.get(SENSORS_URL)
            crit_resp = c.get(CRITICAL_SENSORS_URL)

        if reg_resp.status_code != 200 or crit_resp.status_code != 200:
            raise HTTPException(503, "Failed to fetch sensor data")

        reg_data = reg_resp.json()
        crit_data = crit_resp.json()

        reg = reg_data.get(metric)
        crit = crit_data.get(metric)

        if reg is None or crit is None:
            raise HTTPException(400, f"Metric '{metric}' missing")

        result = verify_metric(
            metric, float(reg), float(crit), transport='http'
        )
        return VerificationResponse(**result)

    except httpx.RequestError as e:
        raise HTTPException(503, f"Sensor fetch failed: {e}")


@app.get('/status')
def status():
    return {
        'service': MODULE_NAME,
        'port': PORT,
        'max_deviation': MAX_DEVIATION,
        'regular_source': SENSORS_URL,
        'critical_source': CRITICAL_SENSORS_URL,
    }


@app.get('/history')
def history(limit: int = 100):
    session = SessionLocal()
    try:
        logs = (
            session.query(VerificationLogDB)
            .order_by(VerificationLogDB.created_at.desc())
            .limit(limit)
            .all()
        )
        return [{
            'id': l.id,
            'metric': l.metric,
            'regular': l.regular_value,
            'critical': l.critical_value,
            'deviation': l.deviation,
            'passed': l.passed,
            'transport': l.transport,
            'created_at': l.created_at.isoformat(),
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info("Starting %s on %s:%s", MODULE_NAME, HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT)