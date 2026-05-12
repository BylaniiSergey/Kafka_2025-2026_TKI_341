import os, logging, random, time, threading
from datetime import datetime, timezone
import uvicorn, httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, Float, Boolean, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 4002))
MODULE_NAME = os.getenv('MODULE_NAME', 'critical_battery_monitor')
STOP_URL = os.getenv('STOP_URL', 'http://stop_module:7001/emergency-stop')
CRITICAL_THRESHOLD = float(os.getenv('CRITICAL_BATTERY_THRESHOLD', '15.0'))  # %
CHECK_INTERVAL = float(os.getenv('CHECK_INTERVAL', '2.0'))  # секунды

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///critical_battery_monitor.db')
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class BatteryAlertDB(Base):
    __tablename__ = 'battery_alerts'
    id = Column(Integer, primary_key=True)
    simulated_soc = Column(Float)
    actual_soc = Column(Float)
    alert_triggered = Column(Boolean)
    stop_signaled = Column(Boolean)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(engine)

def read_critical_sensor() -> float:
    """
    В реальной системе: чтение через ADC с выделенного пина батареи.
    Здесь: эмуляция с небольшим шумом и дрейфом.
    """
    base = 85.0
    drift = -0.01 * time.time() % 100 
    noise = random.gauss(0, 0.5)
    return max(0, min(100, base + drift + noise))

_last_soc = read_critical_sensor()
_alert_active = False

def monitor_loop():
    global _last_soc, _alert_active
    while True:
        time.sleep(CHECK_INTERVAL)
        soc = read_critical_sensor()
        _last_soc = soc
        
        if soc <= CRITICAL_THRESHOLD and not _alert_active:
            _alert_active = True
            logger.critical(f"CRITICAL BATTERY: {soc:.1f}% <= {CRITICAL_THRESHOLD}%")
            try:
                import requests
                resp = requests.post(STOP_URL, json={"reason": "critical_battery"}, timeout=2.0)
                logger.info(f"Emergency stop signaled: {resp.status_code}")
                save_alert(soc, True, True)
            except Exception as e:
                logger.error(f"Failed to signal stop_module: {e}")
                save_alert(soc, True, False)
        elif soc > CRITICAL_THRESHOLD + 5 and _alert_active:
            _alert_active = False
            logger.info(f"Battery recovered: {soc:.1f}%")
            save_alert(soc, False, False)
        else:
            save_alert(soc, False, False)

def save_alert(soc: float, alert: bool, signaled: bool):
    session = SessionLocal()
    try:
        session.add(BatteryAlertDB(simulated_soc=soc, actual_soc=soc, alert_triggered=alert, stop_signaled=signaled))
        session.commit()
    finally: session.close()

threading.Thread(target=monitor_loop, daemon=True).start()

class BatteryStatus(BaseModel):
    soc: float
    critical_threshold: float
    alert_active: bool
    last_check: str

app = FastAPI(title="Critical Battery Monitor", version="2.0")

@app.get('/health')
def health(): return {'status': 'healthy', 'module': MODULE_NAME}

@app.get('/status', response_model=BatteryStatus)
def status():
    return BatteryStatus(
        soc=round(_last_soc, 2),
        critical_threshold=CRITICAL_THRESHOLD,
        alert_active=_alert_active,
        last_check=datetime.now(timezone.utc).isoformat()
    )

@app.get('/raw_reading')
def raw_reading():
    """Прямой доступ к "аппаратному" значению для отладки"""
    return {'soc': read_critical_sensor(), 'timestamp': datetime.now(timezone.utc).isoformat()}

@app.post('/test_alert')
def test_alert():
    """Тестовый триггер для проверки цепочки"""
    global _alert_active
    _alert_active = True
    try:
        import requests
        resp = requests.post(STOP_URL, json={"reason": "test_alert"}, timeout=2.0)
        return {'ok': True, 'stop_response': resp.status_code}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

@app.get('/history')
def history(limit: int = 100):
    session = SessionLocal()
    try:
        alerts = session.query(BatteryAlertDB).order_by(BatteryAlertDB.created_at.desc()).limit(limit).all()
        return [{'id': a.id, 'soc': a.simulated_soc, 'alert': a.alert_triggered, 'signaled': a.stop_signaled, 'created_at': a.created_at.isoformat()} for a in alerts]
    finally: session.close()

if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT} | Threshold: {CRITICAL_THRESHOLD}%")
    uvicorn.run(app, host=HOST, port=PORT)
