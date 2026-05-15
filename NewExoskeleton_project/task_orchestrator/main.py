# task_orchestrator/main.py
import os
import logging
from datetime import datetime

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime
)
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5000))
MODULE_NAME = os.getenv('MODULE_NAME', 'task_orchestrator')

COMMAND_VERIFICATION_URL = os.getenv(
    'COMMAND_VERIFICATION_URL', 'http://localhost:5101'
)
CONTROL_SYSTEM_URL = os.getenv(
    'CONTROL_SYSTEM_URL', 'http://localhost:8000'
)
EMERGENCY_CONTROL_URL = os.getenv(
    'EMERGENCY_CONTROL_URL', 'http://localhost:5201'
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = 'sqlite:///task_orchestrator.db'
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

EMERGENCY_COMMANDS = {'emergency_stop'}


class TaskLogDB(Base):
    __tablename__ = 'task_log'
    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50))
    target = Column(String(50))
    command = Column(String(100))
    trusted = Column(Boolean)
    status = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


class CommandRequest(BaseModel):
    source: str
    command: str
    verification_token: str = ""
    payload: dict = Field(default_factory=dict)


def save_task(
    source: str, target: str,
    command: str, trusted: bool, status: str
):
    session = SessionLocal()
    try:
        session.add(TaskLogDB(
            source=source, target=target,
            command=command, trusted=trusted, status=status
        ))
        session.commit()
    finally:
        session.close()


app = FastAPI(title="Task Orchestrator", version="2.1")


@app.get('/health')
def health():
    return {'status': 'ok', 'service': MODULE_NAME}


@app.get('/status')
def status():
    session = SessionLocal()
    try:
        total = session.query(TaskLogDB).count()
        return {
            'service': MODULE_NAME,
            'total_tasks': total,
            'verification_url': COMMAND_VERIFICATION_URL,
            'control_url': CONTROL_SYSTEM_URL,
            'emergency_url': EMERGENCY_CONTROL_URL
        }
    finally:
        session.close()


@app.post('/dispatch')
def dispatch(body: CommandRequest):
    logger.info(
        f"Dispatch: command={body.command}, source={body.source}"
    )

    # 1. Верификация команды
    try:
        with httpx.Client(timeout=5.0) as c:
            verify_resp = c.post(
                f'{COMMAND_VERIFICATION_URL}/verify',
                json={
                    'command': body.command,
                    'source': body.source,
                    'payload': body.payload,
                    'verification_token': body.verification_token
                }
            )
            verify_resp.raise_for_status()
            verify_data = verify_resp.json()
    except httpx.HTTPStatusError as e:
        save_task(
            body.source, 'rejected',
            body.command, False, 'rejected'
        )
        raise HTTPException(
            status_code=403,
            detail=f'Command verification failed: {e}'
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f'Verification service unavailable: {e}'
        )

    if not verify_data.get('trusted', False):
        save_task(
            body.source, 'rejected',
            body.command, False, 'rejected'
        )
        raise HTTPException(
            status_code=403, detail='Untrusted command'
        )

    # 2. Маршрутизация
    if body.command in EMERGENCY_COMMANDS:
        target = 'emergency_control_module'
        try:
            with httpx.Client(timeout=5.0) as c:
                resp = c.post(
                    f'{EMERGENCY_CONTROL_URL}/emergency',
                    json={
                        'source': body.source,
                        'reason': 'doctor_emergency_command'
                    }
                )
                result = resp.json()
        except Exception as e:
            save_task(
                body.source, target,
                body.command, True, 'error'
            )
            raise HTTPException(
                status_code=502,
                detail=f'Emergency control error: {e}'
            )
    else:
        target = 'control_system'
        try:
            with httpx.Client(timeout=5.0) as c:
                resp = c.post(
                    f'{CONTROL_SYSTEM_URL}/commands',
                    json={
                        'action': body.command,
                        'source': body.source,
                        **body.payload
                    }
                )
                result = resp.json()
        except Exception as e:
            save_task(
                body.source, target,
                body.command, True, 'error'
            )
            raise HTTPException(
                status_code=502,
                detail=f'Control system error: {e}'
            )

    save_task(body.source, target, body.command, True, 'routed')
    logger.info(
        f"Routed '{body.command}' from {body.source} → {target}"
    )

    return {
        'ok': True,
        'target': target,
        'command': body.command,
        'result': result
    }


@app.get('/history')
def get_history(limit: int = Query(100, ge=1, le=1000)):
    session = SessionLocal()
    try:
        logs = (
            session.query(TaskLogDB)
            .order_by(TaskLogDB.created_at.desc())
            .limit(limit).all()
        )
        return [{
            'id': l.id,
            'source': l.source,
            'target': l.target,
            'command': l.command,
            'trusted': l.trusted,
            'status': l.status,
            'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
            if l.created_at else None
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)