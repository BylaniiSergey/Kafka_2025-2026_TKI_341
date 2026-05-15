# critical_situation_recognition/main.py
import os
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float,
    Boolean, String, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5301))
MODULE_NAME = os.getenv(
    'MODULE_NAME', 'critical_situation_recognition'
)

EMERGENCY_CONTROL_URL = os.getenv(
    'EMERGENCY_CONTROL_URL', 'http://localhost:5201'
)

THRESHOLDS = {
    'joint_angle': (0, 150),
    'joint_angular_velocity': (-150, 150),
    'torque': (0, 80),
    'motor_temp': (0, 70),
    'imu_acceleration': (-20, 20),
    'balance_deviation': (-15, 15)
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
    stop_triggered = Column(Boolean)
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc)
    )


Base.metadata.create_all(engine)


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


def save_alert(
    metric: str, value: float,
    t_min: float, t_max: float,
    critical: bool, triggered: bool
):
    session = SessionLocal()
    try:
        session.add(SituationAlertDB(
            metric=metric, value=value,
            threshold_min=t_min, threshold_max=t_max,
            critical=critical, stop_triggered=triggered
        ))
        session.commit()
    finally:
        session.close()


async def trigger_emergency(reason: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.post(
                f"{EMERGENCY_CONTROL_URL}/emergency",
                json={
                    "source": MODULE_NAME,
                    "reason": reason
                }
            )
        logger.critical(
            f"Emergency triggered: {reason}"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to trigger emergency: {e}")
        return False


app = FastAPI(
    title="Critical Situation Recognition", version="2.1"
)


@app.get('/health')
def health():
    return {'status': 'healthy', 'module': MODULE_NAME}


@app.post('/analyze', response_model=AlertResponse)
async def analyze(body: TelemetryInput):
    if not body.sensor_trusted:
        return AlertResponse(
            critical=False,
            action="ignore_untrusted",
            metric=body.metric,
            value=body.value,
            reason="sensor_not_trusted"
        )

    if body.metric not in THRESHOLDS:
        return AlertResponse(
            critical=False,
            action="unknown_metric",
            metric=body.metric,
            value=body.value,
            reason="metric_not_configured"
        )

    t_min, t_max = THRESHOLDS[body.metric]
    is_critical = not (t_min <= body.value <= t_max)

    save_alert(
        body.metric, body.value,
        t_min, t_max, is_critical, False
    )

    if is_critical:
        logger.critical(
            f"CRITICAL: {body.metric}={body.value} "
            f"not in [{t_min}, {t_max}]"
        )
        triggered = await trigger_emergency(
            f"critical_{body.metric}"
        )
        save_alert(
            body.metric, body.value,
            t_min, t_max, True, triggered
        )
        return AlertResponse(
            critical=True,
            action=(
                "emergency_stop_triggered"
                if triggered
                else "emergency_stop_failed"
            ),
            metric=body.metric,
            value=body.value,
            reason="threshold_exceeded"
        )

    return AlertResponse(
        critical=False,
        action="continue",
        metric=body.metric,
        value=body.value,
        reason="within_limits"
    )


@app.post('/batch_analyze')
async def batch_analyze(metrics: list[TelemetryInput]):
    results = []
    any_critical = False
    for m in metrics:
        res = await analyze(m)
        results.append(res)
        if res.critical:
            any_critical = True
    return {
        'results': results,
        'any_critical': any_critical
    }


@app.get('/status')
def status():
    return {
        'service': MODULE_NAME,
        'thresholds': THRESHOLDS,
        'emergency_url': EMERGENCY_CONTROL_URL,
        'last_check': datetime.now(timezone.utc).isoformat()
    }


@app.get('/history')
def history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        alerts = (
            session.query(SituationAlertDB)
            .order_by(SituationAlertDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': a.id,
            'metric': a.metric,
            'value': a.value,
            'critical': a.critical,
            'triggered': a.stop_triggered,
            'created_at': a.created_at.isoformat()
        } for a in alerts]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)