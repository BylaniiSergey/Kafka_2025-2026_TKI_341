import os
import logging
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 5101))
MODULE_NAME = os.getenv('MODULE_NAME', 'command_verification')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///command_verification.db')
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class VerificationLogDB(Base):
    __tablename__ = 'verification_log'

    id = Column(Integer, primary_key=True)
    command = Column(String(100))
    source = Column(String(50))
    passed = Column(Boolean)
    reason = Column(String(200))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(engine)

ALLOWED_COMMANDS = {
    'move_forward', 'move_backward', 'turn_left', 'turn_right',
    'stop', 'brake', 'set_mode', 'adjust_temp', 'navigate',
    'lift_arm', 'lower_arm', 'extend_arm', 'retract_arm'
}
TRUSTED_SOURCES = {'control_system', 'doctor_console', 'task_orchestrator'}
RATE_LIMIT = {src: {'count': 0, 'window_start': None} for src in TRUSTED_SOURCES}
MAX_COMMANDS_PER_MINUTE = 60


class VerifyRequest(BaseModel):
    command: str
    source: str
    payload: dict = Field(default_factory=dict)
    verification_token: str = ""


class VerifyResponse(BaseModel):
    trusted: bool
    token: str
    reason: str
    expires_at: str


def check_rate_limit(source: str) -> bool:
    import time
    now = time.time()
    rl = RATE_LIMIT.get(source)

    if not rl:
        return False

    if rl['window_start'] is None or now - rl['window_start'] > 60:
        RATE_LIMIT[source] = {'count': 1, 'window_start': now}
        return True

    if rl['count'] >= MAX_COMMANDS_PER_MINUTE:
        return False

    RATE_LIMIT[source]['count'] += 1
    return True


def generate_token(command: str, source: str) -> str:
    import hashlib
    import time

    payload = f"{command}:{source}:{int(time.time()) // 60}"
    salt = os.getenv('SECRET_SALT', 'salt')
    return hashlib.sha256((payload + salt).encode()).hexdigest()[:16]


def save_log(command: str, source: str, passed: bool, reason: str):
    session = SessionLocal()
    try:
        session.add(VerificationLogDB(
            command=command,
            source=source,
            passed=passed,
            reason=reason
        ))
        session.commit()
    finally:
        session.close()


app = FastAPI(title="Command Verification", version="2.1")


@app.get('/health')
def health():
    return {'status': 'healthy', 'module': MODULE_NAME, 'port': PORT}


@app.post('/verify', response_model=VerifyResponse)
def verify(body: VerifyRequest):
    if body.source not in TRUSTED_SOURCES:
        save_log(body.command, body.source, False, "untrusted_source")
        raise HTTPException(403, detail="Source not trusted")

    if not check_rate_limit(body.source):
        save_log(body.command, body.source, False, "rate_limit_exceeded")
        raise HTTPException(429, detail="Rate limit exceeded")

    cmd_base = body.command.split('_')[0]
    if cmd_base not in ALLOWED_COMMANDS and body.command not in ALLOWED_COMMANDS:
        save_log(body.command, body.source, False, "unknown_command")
        raise HTTPException(400, detail=f"Unknown command: {body.command}")

    if body.verification_token and not body.verification_token.startswith("SECURE_"):
        save_log(body.command, body.source, False, "invalid_token")
        raise HTTPException(401, detail="Invalid verification token")

    token = f"SECURE_{generate_token(body.command, body.source)}"
    expires = datetime.now(timezone.utc).timestamp() + 300

    save_log(body.command, body.source, True, "verified")
    logger.info("Command verified: %s from %s", body.command, body.source)

    return VerifyResponse(
        trusted=True,
        token=token,
        reason="verified",
        expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat()
    )


@app.get('/policy')
def get_policy():
    return {
        'allowed_commands': sorted(ALLOWED_COMMANDS),
        'trusted_sources': sorted(TRUSTED_SOURCES),
        'rate_limit': f"{MAX_COMMANDS_PER_MINUTE}/minute",
        'port': PORT,
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
            'id': l.id,
            'command': l.command,
            'source': l.source,
            'passed': l.passed,
            'reason': l.reason,
            'created_at': l.created_at.isoformat()
        } for l in logs]
    finally:
        session.close()


if __name__ == '__main__':
    logger.info("Starting %s on %s:%s", MODULE_NAME, HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT)