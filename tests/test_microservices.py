"""
Тесты FastAPI микросервисов (без Docker): импорт по пути services/<имя>/main.py.
"""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
_ROOT = Path(__file__).resolve().parents[1]

def _load_service(name: str):
    path = _ROOT / "services" / name / "main.py"
    spec = importlib.util.spec_from_file_location(f"svc_{name}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

@pytest.fixture
def stop_app():
    return _load_service("stop").app

@pytest.fixture
def gateway_app():
    return _load_service("control_gateway").app

def test_stop_health(stop_app) -> None:
    r = TestClient(stop_app).get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "stop"

def test_stop_emergency_stop(stop_app) -> None:
    c = TestClient(stop_app)
    c.post("/smooth-stop")
    r = c.post("/emergency-stop", json={"reason": "patient_emergency"})
    assert r.status_code == 200
    assert r.json()["state"]["stopped"] is True

def test_gateway_health(gateway_app) -> None:
    r = TestClient(gateway_app).get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "control_gateway"