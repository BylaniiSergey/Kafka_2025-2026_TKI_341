# sensor_verification/main.py
import os
import logging
from datetime import datetime, timezone

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine, Column, Integer, Float,
    Boolean, String, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5302))
MODULE_NAME = os.getenv(
    'MODULE_NAME', 'sensor_verification'
)

SENSORS_URL = os.getenv(
    'SENSORS_URL', 'http://localhost:6003/readings'
)
CRITICAL_SENSORS_URL = os.getenv(
    'CRITICAL_SENSORS_URL', 'http://localhost:5305/readings'
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
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc)
    )


Base.metadata.create_all(engine)


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


def save_log(
    metric: str, reg: float, crit: float,
    dev: float, passed: bool, forwarded: bool
):
    session = SessionLocal()
    try:
        session.add(VerificationLogDB(
            metric=metric,
            regular_value=reg,
            critical_value=crit,
            deviation=dev,
            passed=passed,
            forwarded=forwarded
        ))
        session.commit()
    finally:
        session.close()


app = FastAPI(title="Sensor Verification", version="2.1")


@app.get('/health')
def health():
    return {'status': 'healthy', 'module': MODULE_NAME}


@app.post('/verify', response_model=VerificationResponse)
def verify(body: VerificationRequest):
    deviation = abs(
        body.regular_value - body.critical_value
    )
    passed = deviation <= body.tolerance
    forwarded = body.critical_value if passed else 0.0
    reason = (
        "ok" if passed
        else f"deviation_{deviation:.2f}_exceeds_"
             f"{body.tolerance}"
    )
    save_log(
        body.metric, body.regular_value,
        body.critical_value, deviation,
        passed, passed
    )
    logger.info(
        f"Verify {body.metric}: "
        f"{'PASS' if passed else 'FAIL'} | "
        f"dev={deviation:.2f}"
    )
    return VerificationResponse(
        metric=body.metric,
        passed=passed,
        deviation=round(deviation, 3),
        forwarded_value=forwarded,
        reason=reason
    )


@app.get('/auto_verify')
async def auto_verify(metric: str = "joint_angle"):
    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            reg_resp = await client.get(SENSORS_URL)
            crit_resp = await client.get(CRITICAL_SENSORS_URL)
            if (
                reg_resp.status_code != 200
                or crit_resp.status_code != 200
            ):
                raise HTTPException(
                    503, "Failed to fetch sensor data"
                )

            reg_data = reg_resp.json()
            crit_data = crit_resp.json()

            reg_val = reg_data.get(metric)
            crit_val = crit_data.get(metric)
            if reg_val is None or crit_val is None:
                raise HTTPException(
                    400,
                    f"Metric '{metric}' not found "
                    f"in one of sources"
                )

            return verify(VerificationRequest(
                metric=metric,
                regular_value=reg_val,
                critical_value=crit_val
            ))
        except httpx.RequestError as e:
            raise HTTPException(
                503, f"Sensor fetch failed: {str(e)}"
            )


@app.get('/status')
def status():
    return {
        'service': MODULE_NAME,
        'max_deviation': MAX_DEVIATION,
        'regular_source': SENSORS_URL,
        'critical_source': CRITICAL_SENSORS_URL
    }


@app.get('/history')
def history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(VerificationLogDB)
            .order_by(VerificationLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id,
            'metric': l.metric,
            'regular': l.regular_value,
            'critical': l.critical_value,
            'deviation': l.deviation,
            'passed': l.passed,
            'forwarded': l.forwarded,
            'created_at': l.created_at.isoformat()
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)