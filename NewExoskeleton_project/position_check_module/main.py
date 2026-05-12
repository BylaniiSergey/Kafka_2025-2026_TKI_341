# position_check_module/main.py
import os
import logging
from datetime import datetime
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    Boolean, DateTime, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5005))
MODULE_NAME = os.getenv('MODULE_NAME', 'position_check_module')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

EMERGENCY_CONTROL_URL = os.getenv(
    'EMERGENCY_CONTROL_URL', 'http://localhost:5001'
)
REQUEST_TIMEOUT = 5.0

# Допустимое расхождение ИНС и GNSS
# Если расхождение больше — доверяем ИНС, GNSS считается ошибочным
MAX_DIVERGENCE = 1.5

# Допустимая зона
ZONE_LIMIT = 5

DATABASE_URL = 'sqlite:///position_check.db'
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class PositionCheckEventDB(Base):
    __tablename__ = 'position_check_events'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ins_x = Column(Float, nullable=True)
    ins_y = Column(Float, nullable=True)
    gnss_x = Column(Float, nullable=True)
    gnss_y = Column(Float, nullable=True)
    divergence = Column(Float, nullable=True)
    ins_in_zone = Column(Boolean, nullable=True)
    gnss_in_zone = Column(Boolean, nullable=True)
    trusted_source = Column(String(10))  # 'ins' always
    alert_sent = Column(Boolean, default=False)
    alert_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)

# Последние известные позиции
position_state = {
    'ins': {'x': None, 'y': None, 'in_zone': True},
    'gnss': {'x': None, 'y': None, 'in_zone': True},
    'last_divergence': None,
    'alert_active': False,
    'total_alerts': 0
}


class PositionUpdateRequest(BaseModel):
    x: float
    y: float
    in_zone: bool
    intent: Optional[str] = None


def compute_divergence() -> Optional[float]:
    ix = position_state['ins']['x']
    iy = position_state['ins']['y']
    gx = position_state['gnss']['x']
    gy = position_state['gnss']['y']
    if None in (ix, iy, gx, gy):
        return None
    return ((ix - gx) ** 2 + (iy - gy) ** 2) ** 0.5


def send_emergency(reason: str):
    """Отправить сигнал в emergency_control_module"""
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as c:
            resp = c.post(
                f'{EMERGENCY_CONTROL_URL}/emergency',
                json={
                    'source': MODULE_NAME,
                    'reason': reason
                }
            )
            logger.critical(
                f"Emergency sent: {reason}, "
                f"response={resp.status_code}"
            )
            return resp.json()
    except Exception as e:
        logger.error(f"Failed to send emergency: {e}")
        return {'error': str(e)}


def evaluate_and_alert() -> dict:
    """
    Логика комплексирования:
    - ИНС всегда является эталоном.
    - GNSS может ошибаться.
    - При расхождении > MAX_DIVERGENCE → доверяем ИНС.
    - Если ИНС говорит, что вышли за зону → аварийный сигнал.
    - Если GNSS говорит, что вышли, но ИНС — нет → предупреждение,
      но аварии нет (GNSS мог ошибиться).
    """
    divergence = compute_divergence()
    position_state['last_divergence'] = divergence

    alerts = []
    alert_sent = False
    emergency_result = None

    # 1. Проверка расхождения
    if divergence is not None and divergence > MAX_DIVERGENCE:
        alerts.append(
            f'INS/GNSS divergence: {divergence:.2f} '
            f'(max={MAX_DIVERGENCE}). Trusting INS.'
        )
        logger.warning(
            f"INS/GNSS divergence={divergence:.2f}, "
            f"trusting INS"
        )

    # 2. ИНС говорит, что вышли за зону → аварийный сигнал
    ins_out = not position_state['ins'].get('in_zone', True)
    if ins_out:
        reason = (
            f'INS: position out of authorized zone '
            f"(x={position_state['ins']['x']:.1f}, "
            f"y={position_state['ins']['y']:.1f})"
        )
        alerts.append(reason)
        position_state['alert_active'] = True
        position_state['total_alerts'] += 1
        emergency_result = send_emergency(reason)
        alert_sent = True
        logger.critical(f"ZONE BREACH (INS): {reason}")

    # 3. GNSS говорит, что вышли, но ИНС — нет → только лог
    gnss_out = not position_state['gnss'].get('in_zone', True)
    if gnss_out and not ins_out:
        logger.warning(
            "GNSS reports out of zone, but INS is OK. "
            "Trusting INS — no emergency."
        )
        alerts.append('GNSS zone mismatch (ignored, INS is reference)')

    # Сохранить событие
    session = SessionLocal()
    try:
        session.add(PositionCheckEventDB(
            ins_x=position_state['ins']['x'],
            ins_y=position_state['ins']['y'],
            gnss_x=position_state['gnss']['x'],
            gnss_y=position_state['gnss']['y'],
            divergence=divergence,
            ins_in_zone=position_state['ins']['in_zone'],
            gnss_in_zone=position_state['gnss']['in_zone'],
            trusted_source='ins',
            alert_sent=alert_sent,
            alert_reason='; '.join(alerts) if alerts else None
        ))
        session.commit()
    finally:
        session.close()

    return {
        'divergence': divergence,
        'ins_in_zone': position_state['ins']['in_zone'],
        'gnss_in_zone': position_state['gnss']['in_zone'],
        'alert_sent': alert_sent,
        'alerts': alerts,
        'emergency_result': emergency_result
    }


app = FastAPI(title="Position Check Module", version="1.0")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def get_status():
    divergence = compute_divergence()
    return {
        'service': MODULE_NAME,
        'ins_position': position_state['ins'],
        'gnss_position': position_state['gnss'],
        'divergence': divergence,
        'max_divergence': MAX_DIVERGENCE,
        'zone_limit': ZONE_LIMIT,
        'alert_active': position_state['alert_active'],
        'total_alerts': position_state['total_alerts'],
        'trusted_source': 'ins'
    }


@app.post('/ins_update')
def ins_update(body: PositionUpdateRequest):
    """
    Получить обновление позиции от ИНС.
    ИНС является эталонным источником.
    """
    position_state['ins']['x'] = body.x
    position_state['ins']['y'] = body.y
    position_state['ins']['in_zone'] = body.in_zone

    logger.info(
        f"INS update: x={body.x:.1f}, y={body.y:.1f}, "
        f"in_zone={body.in_zone}"
    )

    result = evaluate_and_alert()
    return {
        'ok': True,
        'source': 'ins',
        'position': {'x': body.x, 'y': body.y},
        'evaluation': result
    }


@app.post('/gnss_update')
def gnss_update(body: PositionUpdateRequest):
    """
    Получить обновление позиции от GNSS.
    GNSS может ошибаться — используется только для
    сравнения с ИНС.
    """
    position_state['gnss']['x'] = body.x
    position_state['gnss']['y'] = body.y
    position_state['gnss']['in_zone'] = body.in_zone

    logger.info(
        f"GNSS update: x={body.x:.1f}, y={body.y:.1f}, "
        f"in_zone={body.in_zone}"
    )

    result = evaluate_and_alert()
    return {
        'ok': True,
        'source': 'gnss',
        'position': {'x': body.x, 'y': body.y},
        'evaluation': result
    }


@app.post('/reset_alert')
def reset_alert(source: str = 'operator'):
    """Сбросить аварийный флаг"""
    position_state['alert_active'] = False
    logger.info(f"Alert reset by {source}")
    return {'ok': True, 'alert_active': False}


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        events = (
            session.query(PositionCheckEventDB)
            .order_by(PositionCheckEventDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': e.id,
            'ins_x': e.ins_x,
            'ins_y': e.ins_y,
            'gnss_x': e.gnss_x,
            'gnss_y': e.gnss_y,
            'divergence': e.divergence,
            'ins_in_zone': e.ins_in_zone,
            'gnss_in_zone': e.gnss_in_zone,
            'trusted_source': e.trusted_source,
            'alert_sent': e.alert_sent,
            'alert_reason': e.alert_reason,
            'created_at': e.created_at.strftime('%Y-%m-%d %H:%M:%S')
            if e.created_at else None
        } for e in events]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)