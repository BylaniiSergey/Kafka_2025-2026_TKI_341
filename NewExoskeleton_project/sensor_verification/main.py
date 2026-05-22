import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import uvicorn
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine, Column, Integer, Float, Boolean, String, DateTime,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from kafka_bus import EventBus, TOPIC_SENSORS_RAW, TOPIC_SENSORS_VERIFIED
from logging_config import setup_logging

# ── Конфигурация ──────────────────────────────────────────────────────────────

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5103))
MODULE_NAME = os.getenv('MODULE_NAME', 'sensor_verification')

SENSORS_URL = os.getenv(
    'SENSORS_URL', 'http://localhost:6003/readings'
)
CRITICAL_SENSORS_URL = os.getenv(
    'CRITICAL_SENSORS_URL', 'http://localhost:4003/readings'
)

MAX_DEVIATION = float(os.getenv('MAX_DEVIATION', '150.0'))
FAIL_THRESHOLD = int(os.getenv('FAIL_THRESHOLD', '3'))

# ── Логирование ───────────────────────────────────────────────────────────────

setup_logging()
logger = logging.getLogger(MODULE_NAME)

# ── База данных ───────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    'DATABASE_URL', 'sqlite:///sensor_verification.db'
)
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
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
    created_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


Base.metadata.create_all(engine)

# ── Kafka ─────────────────────────────────────────────────────────────────────

bus = EventBus(client_id=MODULE_NAME)

# ── Счётчики последовательных сбоев ───────────────────────────────────────────

_consecutive_failures: dict[str, int] = {}

# ── Счётчик обработанных сообщений (для лога) ─────────────────────────────────

_message_count = 0


# ── Pydantic модели ───────────────────────────────────────────────────────────

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


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _save_log(metric, reg, crit, dev, passed, forwarded, transport):
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
    except Exception as e:
        logger.error("DB write failed: %s", e)
        session.rollback()
    finally:
        session.close()


def _fetch_critical_snapshot() -> dict:
    try:
        with httpx.Client(timeout=3.0) as c:
            resp = c.get(CRITICAL_SENSORS_URL)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.warning(
                    "Critical sensors returned HTTP %d", resp.status_code
                )
    except Exception as e:
        logger.debug("Critical sensors fetch failed: %s", e)
    return {}


def _normalize_critical_data(critical: dict) -> dict:
    KEY_MAP = {
        'angle': 'joint_angle',
        'joint_angle': 'joint_angle',
        'velocity': 'joint_angular_velocity',
        'angular_velocity': 'joint_angular_velocity',
        'joint_angular_velocity': 'joint_angular_velocity',
        'torque': 'torque',
        'motor_temp': 'motor_temp',
        'temperature': 'motor_temp',
        'roll': 'imu_roll',
        'imu_roll': 'imu_roll',
        'pitch': 'imu_pitch',
        'imu_pitch': 'imu_pitch',
        'yaw': 'imu_yaw',
        'imu_yaw': 'imu_yaw',
    }
    normalized = {}
    for raw_key, value in critical.items():
        standard_key = KEY_MAP.get(raw_key, raw_key)
        normalized[standard_key] = value
    return normalized


def _verify_metric(
    metric: str,
    regular: float,
    critical: float,
    tolerance: float = MAX_DEVIATION,
    transport: str = 'http',
) -> dict:
    if math.isnan(regular) or math.isinf(regular):
        regular = 0.0
    if math.isnan(critical) or math.isinf(critical):
        critical = regular

    deviation = abs(regular - critical)
    passed = deviation <= tolerance

    _save_log(
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
    """
    Обработчик сообщений из exo.sensors.raw.
    Вызывается каждые 20 секунд (интервал sensors_module).
    """
    global _message_count
    _message_count += 1

    # Получаем данные критических датчиков
    raw_critical = _fetch_critical_snapshot()
    critical = _normalize_critical_data(raw_critical)

    verified = {'trusted': True}
    failed_metrics = []

    # Перекрёстная верификация
    cross_check_metrics = ('joint_angle', 'joint_angular_velocity')

    for metric in cross_check_metrics:
        if metric not in payload:
            continue

        reg = payload[metric]

        if metric not in critical:
            verified[metric] = reg
            _consecutive_failures[metric] = 0
            continue

        crit = critical[metric]
        result = _verify_metric(
            metric, float(reg), float(crit),
            tolerance=MAX_DEVIATION,
            transport='kafka',
        )

        if result['passed']:
            verified[metric] = reg
            _consecutive_failures[metric] = 0
        else:
            _consecutive_failures[metric] = (
                _consecutive_failures.get(metric, 0) + 1
            )
            count = _consecutive_failures[metric]

            if count >= FAIL_THRESHOLD:
                failed_metrics.append(metric)
                verified[metric] = None
            else:
                verified[metric] = reg

    # Проходные метрики
    for metric in ('torque', 'motor_temp', 'imu_roll', 'imu_pitch', 'imu_yaw'):
        if metric in payload:
            verified[metric] = payload[metric]

    # Итоговый статус
    if failed_metrics:
        verified['trusted'] = False
        logger.warning(
            "Verification #%d FAILED — metrics: %s",
            _message_count, failed_metrics,
        )
    else:
        verified['trusted'] = True
        # Логируем каждую успешную верификацию
        logger.info(
            "Verification #%d OK | angle=%.1f vel=%.1f trusted=True",
            _message_count,
            verified.get('joint_angle', 0),
            verified.get('joint_angular_velocity', 0),
        )

    bus.publish(TOPIC_SENSORS_VERIFIED, verified)


# ── FastAPI ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s on %s:%s", MODULE_NAME, HOST, PORT)
    bus.subscribe(
        TOPIC_SENSORS_RAW,
        handler=_on_raw_sensor_message,
        group_id='sensor-verification',
    )
    yield
    bus.close()
    logger.info("%s stopped", MODULE_NAME)


app = FastAPI(
    title="Sensor Verification",
    version="3.3",
    lifespan=lifespan,
)


@app.get('/health')
def health():
    return {
        'status': 'healthy',
        'module': MODULE_NAME,
        'messages_processed': _message_count,
    }


@app.post('/verify', response_model=VerificationResponse)
def verify(body: VerificationRequest):
    result = _verify_metric(
        body.metric, body.regular_value, body.critical_value,
        tolerance=body.tolerance, transport='http',
    )
    logger.info(
        "Manual verify %s: %s | dev=%.3f",
        body.metric,
        'PASS' if result['passed'] else 'FAIL',
        result['deviation'],
    )
    return VerificationResponse(**result)


@app.get('/auto_verify')
def auto_verify(metric: str = "joint_angle"):
    try:
        with httpx.Client(timeout=3.0) as c:
            reg_resp = c.get(SENSORS_URL)
            crit_resp = c.get(CRITICAL_SENSORS_URL)

        if reg_resp.status_code != 200 or crit_resp.status_code != 200:
            raise HTTPException(503, "Failed to fetch sensor data")

        reg_data = reg_resp.json()
        crit_data = _normalize_critical_data(crit_resp.json())

        reg = reg_data.get(metric)
        crit = crit_data.get(metric)

        if reg is None:
            raise HTTPException(
                400, f"Metric '{metric}' missing in sensors_module"
            )
        if crit is None:
            raise HTTPException(
                400,
                f"Metric '{metric}' missing in critical_sensors. "
                f"Available: {list(crit_data.keys())}",
            )

        result = _verify_metric(
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
        'fail_threshold': FAIL_THRESHOLD,
        'messages_processed': _message_count,
        'consecutive_failures': dict(_consecutive_failures),
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
            'id': log.id,
            'metric': log.metric,
            'regular': log.regular_value,
            'critical': log.critical_value,
            'deviation': log.deviation,
            'passed': log.passed,
            'transport': log.transport,
            'created_at': log.created_at.isoformat(),
        } for log in logs]
    finally:
        session.close()


@app.get('/debug/critical_keys')
def debug_critical_keys():
    raw = _fetch_critical_snapshot()
    normalized = _normalize_critical_data(raw)
    return {
        'raw_keys': list(raw.keys()),
        'normalized_keys': list(normalized.keys()),
        'joint_angle_found': 'joint_angle' in normalized,
        'velocity_found': 'joint_angular_velocity' in normalized,
    }


# ── Запуск ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logger.info("Starting %s on %s:%s", MODULE_NAME, HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, access_log=False)