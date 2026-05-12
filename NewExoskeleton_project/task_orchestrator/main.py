import os
import logging
import uvicorn
from datetime import datetime
from enum import Enum
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

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
    status = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)

try:
    Base.metadata.create_all(engine)
    logger.info("Database tables created successfully")
except Exception as e:
    logger.error(f"Database initialization error: {e}")

class CommandRequest(BaseModel):
    source: str
    target: str
    command: str
    verification_token: str = ""

app = FastAPI(title="Task Orchestrator", version="2.0")

@app.get('/health')
def health(): 
    return {'status': 'ok', 'service': MODULE_NAME}

@app.get('/status')
def status(): 
    return {'service': 'orchestrator', 'active_tasks': 0, 'trust_gateway': 'enabled'}

@app.post('/dispatch')
def dispatch(body: CommandRequest):
    is_trusted = body.verification_token == "SECURE_ROUTE"
    status = TaskStatus.ROUTED.value if is_trusted else TaskStatus.REJECTED.value
    
    if not is_trusted:
        logger.warning(f"Command rejected: {body.command} from {body.source}")
        raise HTTPException(status_code=403, detail="Untrusted command blocked")

    session = SessionLocal()
    try:
        task_log = TaskLogDB(
            source=body.source, 
            target=body.target, 
            command=body.command, 
            trusted=is_trusted, 
            status=status
        )
        session.add(task_log)
        session.commit()
        logger.info(f"Command '{body.command}' logged with id {task_log.id}")
    except Exception as e:
        session.rollback()
        logger.error(f"Database error: {e}")
        raise HTTPException(status_code=500, detail="Internal database error")
    finally:
        session.close()

    logger.info(f"Routed command '{body.command}' to {body.target}")
    return {'ok': True, 'status': status, 'target': body.target}

if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(
        app, 
        host=HOST, 
        port=PORT,
        log_level="info"
    )
