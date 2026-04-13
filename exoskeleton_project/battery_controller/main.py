# battery_controller/main.py
import os
import logging
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float, Boolean, String, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 6004))
MODULE_NAME = os.getenv('MODULE_NAME', 'battery_controller')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

CHARGER_URL = os.getenv('CHARGER_URL', 'http://localhost:6005')
BATTERY_CELL_URL = os.getenv('BATTERY_CELL_URL', 'http://localhost:6006')
REQUEST_TIMEOUT = 5.0

DATABASE_URL = 'sqlite:///battery_controller.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class BatteryControlLogDB(Base):
    __tablename__ = 'battery_control_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(50))
    soc = Column(Float, nullable=True)
    charging_enabled = Column(Boolean, nullable=True)
    success = Column(Boolean)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


class ChargeControlRequest(BaseModel):
    enable: bool


def save_log(action: str, success: bool, soc: float = None,
             charging: bool = None):
    session = SessionLocal()
    try:
        session.add(BatteryControlLogDB(
            action=action, soc=soc,
            charging_enabled=charging, success=success
        ))
        session.commit()
    finally:
        session.close()


app = FastAPI(title="Battery Controller", version="2.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def get_battery_status():
    """
    Контроллер заряда батареи:
    battery_controller → charger_module + battery_cell
    """
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            charger_resp = c.get(f'{CHARGER_URL}/status')
            cell_resp = c.get(f'{BATTERY_CELL_URL}/status')

        charger_data = charger_resp.json()
        cell_data = cell_resp.json()

        status = {
            'soc': cell_data['soc'],
            'soh': cell_data['soh'],
            'voltage': cell_data['voltage'],
            'current': cell_data['current'],
            'temperature': cell_data['temperature'],
            'charger_plugged': charger_data['plugged'],
            'charging_enabled': charger_data['enabled'],
            'charger_voltage': charger_data['voltage'],
            'charger_current_ma': charger_data['current_ma']
        }

        logger.info(
            f"Battery status: soc={status['soc']:.1f}%, "
            f"plugged={status['charger_plugged']}"
        )
        save_log('status', True, soc=status['soc'],
                 charging=status['charging_enabled'])
        return status

    except Exception as e:
        logger.error(f"Battery status error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@app.post('/control/charge')
def control_charge(body: ChargeControlRequest):
    """
    Управление зарядкой:
    battery_controller → charger_module
    """
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(
                f'{CHARGER_URL}/control',
                json={'enabled': body.enable}
            )
        result = resp.json()
        logger.info(
            f"Charge control: {'enabled' if body.enable else 'disabled'}"
        )
        save_log('charge_control', True, charging=body.enable)
        return result
    except Exception as e:
        logger.error(f"Charge control error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@app.post('/discharge')
def trigger_discharge(current_ma: float = 500, duration_ms: int = 1000):
    """
    Симуляция разряда:
    battery_controller → battery_cell
    """
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(
                f'{BATTERY_CELL_URL}/discharge',
                json={
                    'current_ma': current_ma,
                    'duration_ms': duration_ms
                }
            )
        result = resp.json()
        save_log('discharge', True, soc=result.get('new_soc'))
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(BatteryControlLogDB)
            .order_by(BatteryControlLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id, 'action': l.action,
            'soc': l.soc, 'charging_enabled': l.charging_enabled,
            'success': l.success,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
                if l.created_at else None
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)