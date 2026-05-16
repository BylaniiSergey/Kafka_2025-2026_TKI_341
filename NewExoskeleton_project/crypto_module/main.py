import os, logging, base64, hashlib, hmac
from datetime import datetime, timezone
from typing import Optional
import uvicorn
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

HOST = '0.0.0.0'
PORT = int(os.getenv('PORT', 4001))
MODULE_NAME = os.getenv('MODULE_NAME', 'crypto_module')
MASTER_KEY = os.getenv('CRYPTO_MASTER_KEY', 'test-key-do-not-use-in-prod-32chars!!').encode()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(MODULE_NAME)

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///crypto_module.db')
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class CryptoLogDB(Base):
    __tablename__ = 'crypto_log'
    id = Column(Integer, primary_key=True)
    operation = Column(String(20))  
    source_module = Column(String(50))
    target_module = Column(String(50))
    success = Column(Boolean)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(engine)

try:
    fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(MASTER_KEY).digest()))
except Exception as e:
    logger.critical(f"Failed to init crypto: {e}")
    fernet = None

class EncryptRequest(BaseModel):
    plaintext: str
    source: str
    target: str

class DecryptRequest(BaseModel):
    ciphertext: str
    source: str
    target: str
    signature: str

class EncryptResponse(BaseModel):
    ciphertext: str
    signature: str
    timestamp: str

def sign(data: str, key: bytes) -> str:
    return hmac.new(key, data.encode(), hashlib.sha256).hexdigest()

def save_log(op: str, src: str, tgt: str, success: bool):
    session = SessionLocal()
    try:
        session.add(CryptoLogDB(operation=op, source_module=src, target_module=tgt, success=success))
        session.commit()
    finally: session.close()

app = FastAPI(title="Crypto Module", version="2.0")

@app.get('/health')
def health(): return {'status': 'healthy', 'module': MODULE_NAME}

@app.post('/encrypt', response_model=EncryptResponse)
def encrypt(body: EncryptRequest):
    if not fernet: raise HTTPException(503, "Crypto subsystem unavailable")
    try:
        ciphertext = fernet.encrypt(body.plaintext.encode()).decode()
        signature = sign(ciphertext, MASTER_KEY)
        save_log('encrypt', body.source, body.target, True)
        logger.debug(f"Encrypted {body.source}→{body.target}")
        return EncryptResponse(ciphertext=ciphertext, signature=signature, timestamp=datetime.now(timezone.utc).isoformat())
    except Exception as e:
        save_log('encrypt', body.source, body.target, False)
        raise HTTPException(500, f"Encryption failed: {str(e)}")

@app.post('/decrypt')
def decrypt(body: DecryptRequest):
    if not fernet: raise HTTPException(503, "Crypto subsystem unavailable")
    if not hmac.compare_digest(sign(body.ciphertext, MASTER_KEY), body.signature):
        save_log('decrypt', body.source, body.target, False)
        raise HTTPException(403, "Signature verification failed")
    try:
        plaintext = fernet.decrypt(body.ciphertext.encode()).decode()
        save_log('decrypt', body.source, body.target, True)
        return {'plaintext': plaintext, 'verified': True}
    except Exception as e:
        save_log('decrypt', body.source, body.target, False)
        raise HTTPException(400, f"Decryption failed: {str(e)}")

@app.post('/verify_signature')
def verify_signature(data: str, signature: str, source: str = Header(...)):
    is_valid = hmac.compare_digest(sign(data, MASTER_KEY), signature)
    logger.debug(f"Signature verify from {source}: {is_valid}")
    return {'valid': is_valid, 'source': source}

if __name__ == '__main__':
    logger.info(f"Starting {MODULE_NAME} on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)
