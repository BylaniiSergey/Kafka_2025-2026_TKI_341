import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import random
import time
import threading
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Float, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

from kafka_bus import EventBus, TOPIC_EMERGENCY

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 4002))
MODULE_NAME = os.getenv('MODULE_NAME', 'critical_battery_monitor')
CRITICAL_THRESHOLD = float(os.getenv('CRITICAL_BATTERY_THRESHOLD', '15.0'))
CHECK_INTERVAL = float(os.getenv('CHECK_INTERVAL', '2.0'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///critical_battery_monitor.db')
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class BatteryAlertDB(Base):
    __tablename__ = 'battery_alerts'
    id = Column(Integer, primary_key=True)
    soc = Column(Float)
    alert_triggered = Column(Boolean)
    kafka_published = Column(Boolean)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(engine)
bus = EventBus(client_id=MODULE_NAME)


def read_critical_sensor() -> float:
    base = 85.0
    drift = -0.01 * time.time() % 100
    noise = random.gauss(0, 0.5)
    return max(0, min(100, base + drift + noise))


_last_soc = read_critical_sensor()
_alert_active = False


def save_alert(soc: float, alert: bool, published: bool):
    session = SessionLocal()
    try:
        session.add(BatteryAlertDB(soc=soc, alert_triggered=alert, kafka_published=published))
        session.commit()
    finally:
        session.close()


def monitor_loop():
    global _last_soc, _alert_active
    while True:
        time.sleep(CHECK_INTERVAL)
        soc = read_critical_sensor()
        _last_soc = soc
        if soc <= CRITICAL_THRESHOLD and not _alert_active:
            _alert_active = True
            logger.critical(f"CRITICAL BATTERY: {soc:.1f}%")
            published = bus.publish(TOPIC_EMERGENCY, {'source': MODULE_NAME, 'reason': 'critical_battery', 'soc': round(soc, 2)})
            save_alert(soc, True, published)
        elif soc > CRITICAL_THRESHOLD + 5 and _alert_active:
            _alert_active = False
            save_alert(soc, False, False)
        else:
            save_alert(soc, False, False)


threading.Thread(target=monitor_loop, daemon=True).start()


class BatteryStatus(BaseModel):
    soc: float
    critical_threshold: float
    alert_active: bool
    last_check: str


app = FastAPI(title="Critical Battery Monitor", version="3.0")


@app.get('/health')
def health():
    return {'status': 'healthy', 'module': MODULE_NAME}


@app.get('/status', response_model=BatteryStatus)
def status():
    return BatteryStatus(soc=round(_last_soc, 2), critical_threshold=CRITICAL_THRESHOLD,
                         alert_active=_alert_active, last_check=datetime.now(timezone.utc).isoformat())


@app.get('/raw_reading')
def raw_reading():
    return {'soc': read_critical_sensor(), 'timestamp': datetime.now(timezone.utc).isoformat()}


@app.post('/test_alert')
def test_alert():
    global _alert_active
    _alert_active = True
    published = bus.publish(TOPIC_EMERGENCY, {'source': MODULE_NAME, 'reason': 'critical_battery_test', 'soc': _last_soc})
    return {'ok': True, 'published': published}


@app.get('/history')
def history(limit: int = 100):
    session = SessionLocal()
    try:
        alerts = session.query(BatteryAlertDB).order_by(BatteryAlertDB.created_at.desc()).limit(limit).all()
        return [{'id': a.id, 'soc': a.soc, 'alert': a.alert_triggered,
                 'kafka_published': a.kafka_published, 'created_at': a.created_at.isoformat()} for a in alerts]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)