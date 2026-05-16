import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import uvicorn
from datetime import datetime
from enum import Enum
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

from kafka_bus import EventBus, TOPIC_COMMANDS, TOPIC_EMERGENCY

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5000))
MODULE_NAME = os.getenv('MODULE_NAME', 'task_orchestrator')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///task_orchestrator.db'
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class TaskStatus(str, Enum):
    PENDING = "pending"
    ROUTED = "routed"
    COMPLETED = "completed"
    REJECTED = "rejected"


class TaskLogDB(Base):
    __tablename__ = 'task_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50))
    target = Column(String(50))
    command = Column(String(100))
    trusted = Column(Boolean)
    emergency = Column(Boolean, default=False)
    status = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)
bus = EventBus(client_id=MODULE_NAME)

EMERGENCY_COMMANDS = {'emergency_stop', 'stop_emergency', 'abort', 'halt'}


class CommandRequest(BaseModel):
    source: str
    target: str
    command: str
    payload: dict = {}
    verification_token: str = ""


app = FastAPI(title="Task Orchestrator", version="3.0")


@app.on_event('shutdown')
def on_shutdown():
    bus.close()


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def status():
    return {'service': MODULE_NAME, 'trust_gateway': 'enabled', 'bus': 'kafka'}


@app.post('/dispatch')
def dispatch(body: CommandRequest):
    is_trusted = body.verification_token.startswith("SECURE_")
    if not is_trusted:
        save_log(body, trusted=False, emergency=False, status=TaskStatus.REJECTED.value)
        raise HTTPException(403, detail="Untrusted command blocked")
    emergency = body.command.lower() in EMERGENCY_COMMANDS
    topic = TOPIC_EMERGENCY if emergency else TOPIC_COMMANDS
    payload = {'source': body.source, 'target': body.target,
               'command': body.command, 'data': body.payload}
    if emergency:
        payload['reason'] = body.command
    published = bus.publish(topic, payload)
    if not published:
        save_log(body, trusted=True, emergency=emergency, status='kafka_unavailable')
        raise HTTPException(503, "Kafka unavailable")
    save_log(body, trusted=True, emergency=emergency, status=TaskStatus.ROUTED.value)
    return {'ok': True, 'status': TaskStatus.ROUTED.value, 'topic': topic, 'target': body.target}


def save_log(body: CommandRequest, trusted: bool, emergency: bool, status: str):
    session = SessionLocal()
    try:
        session.add(TaskLogDB(source=body.source, target=body.target, command=body.command,
                               trusted=trusted, emergency=emergency, status=status))
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"DB error: {e}")
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")