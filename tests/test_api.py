from __future__ import annotations

from fastapi.testclient import TestClient

from exoskeleton.api.app import create_app
from exoskeleton.control_system import ExoskeletonControlSystem


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_telemetry_initial(client: TestClient) -> None:
    r = client.get("/telemetry")
    assert r.status_code == 200
    data = r.json()
    assert "state" in data
    assert data["session_active"] is False


def test_initialize_and_start_session(client: TestClient) -> None:
    r = client.post("/commands", json={"action": "initialize"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["snapshot"]["state"] == "ready"

    r2 = client.post("/commands", json={"action": "start_session", "source": "patient"})
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    assert r2.json()["snapshot"]["session_active"] is True


def test_emergency_stop_returns_200_with_event(client: TestClient) -> None:
    client.post("/commands", json={"action": "initialize"})
    client.post("/commands", json={"action": "start_session", "source": "patient"})
    r = client.post("/commands", json={"action": "emergency_stop", "source": "patient"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["snapshot"]["state"] == "emergency"
    assert body.get("event", {}).get("type") == "emergency_stop"


def test_invalid_command_422(client: TestClient) -> None:
    r = client.post("/commands", json={"action": "unknown_xyz"})
    assert r.status_code == 422
    assert r.json()["ok"] is False
    assert "error" in r.json()


def test_update_climate(client: TestClient) -> None:
    client.post("/commands", json={"action": "initialize"})
    r = client.post(
        "/commands",
        json={"action": "update_climate", "body_temp_c": 37.5, "air_temp_c": 30.0},
    )
    assert r.status_code == 200
    assert r.json()["result"]["climate_mode"] == "cooling"


def test_untrusted_source_start_session() -> None:
    bad = ExoskeletonControlSystem(trusted_sources=frozenset())
    c = TestClient(create_app(bad))
    c.post("/commands", json={"action": "initialize"})
    r = c.post("/commands", json={"action": "start_session", "source": "patient"})
    assert r.status_code == 422
    assert r.json()["ok"] is False
    assert r.json()["snapshot"]["state"] == "emergency"
