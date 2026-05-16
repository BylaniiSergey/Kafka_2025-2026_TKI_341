
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
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


class _E2EEnv:
    def __init__(self):
        self.stop_mod = _load_service("stop")
        self.carriage_mod = _load_service("carriage")
        self.tactile_mod = _load_service("tactile")
        self.temperature_mod = _load_service("temperature")
        self.heating_mod = _load_service("heating")
        self.cooling_mod = _load_service("cooling")
        self.gateway_mod = _load_service("control_gateway")

        self.stop = TestClient(self.stop_mod.app)
        self.carriage = TestClient(self.carriage_mod.app)
        self.tactile = TestClient(self.tactile_mod.app)
        self.temperature = TestClient(self.temperature_mod.app)
        self.heating = TestClient(self.heating_mod.app)
        self.cooling = TestClient(self.cooling_mod.app)
        self.gateway = TestClient(self.gateway_mod.app)
        self._patch_gateway()

    def _patch_gateway(self):
        # Gateway settings: URL вида http://stop:8000 → маппим на TestClient
        self.gateway_mod.settings.stop = "http://stop:8000"
        self.gateway_mod.settings.carriage = "http://carriage:8000"
        self.gateway_mod.settings.tactile = "http://tactile:8000"
        self.gateway_mod.settings.temperature = "http://temperature:8000"
        self.gateway_mod.settings.heating = "http://heating:8000"
        self.gateway_mod.settings.cooling = "http://cooling:8000"

        clients = {
            "http://stop:8000": self.stop,
            "http://carriage:8000": self.carriage,
            "http://tactile:8000": self.tactile,
            "http://temperature:8000": self.temperature,
            "http://heating:8000": self.heating,
            "http://cooling:8000": self.cooling,
        }

        class _Router(httpx.BaseTransport):
            def handle_request(transport_self, request):
                url = str(request.url)
                for prefix, client in clients.items():
                    if url.startswith(prefix):
                        path = url[len(prefix):] or "/"
                        raw_body = request.read()
                        json_body = None
                        if raw_body:
                            try:
                                json_body = json.loads(raw_body)
                            except Exception:
                                json_body = raw_body.decode()
                        if request.method == "GET":
                            resp = client.request("GET", path)
                        elif request.method == "POST":
                            resp = client.request("POST", path, json=json_body)
                        else:
                            resp = client.request(request.method, path, json=json_body)
                        return httpx.Response(
                            status_code=resp.status_code,
                            headers={"content-type": "application/json"},
                            content=resp.content,
                            request=request,
                        )
                return httpx.Response(502, content=f"unknown upstream: {url}".encode())

        transport = _Router()

        def _mock_client():
            return httpx.Client(transport=transport, timeout=10.0)

        self.gateway_mod._client = _mock_client

    def reset(self):
        self.stop_mod._mod = self.stop_mod.StopModule()
        self.carriage_mod._mod = self.carriage_mod.CarriageSystem()
        self.tactile_mod._mod = self.tactile_mod.TactileModule()
        self.temperature_mod._mod = self.temperature_mod.InternalTemperatureControl()
        self.heating_mod._mod = self.heating_mod.HeatingSystem()
        self.cooling_mod._mod = self.cooling_mod.CoolingSystem()
        self.gateway_mod.state = self.gateway_mod.GatewayState()
        self._patch_gateway()

    def gw(self, action: str, **extra) -> dict:
        body = {"action": action, **extra}
        r = self.gateway.post("/commands", json=body)
        return r.json()

    def stop_status(self) -> dict:
        return self.stop.get("/status").json()


@pytest.fixture
def e2e():
    env = _E2EEnv()
    env.reset()
    yield env


class TestE2E_ЦБ1:
    def test_untrusted_source_rejected(self, e2e):
        result = e2e.gw("start_session", source="hacker")
        assert result["ok"] is False, "ЦБ1: хакер не может начать сессию"
        ss = e2e.stop_status()
        assert ss["stopped"] is True, "ЦБ1: после атаки система остановлена"


class TestE2E_ЦБ2:
    def test_reset_emergency_requires_auth(self, e2e):
        e2e.gw("emergency_stop", source="patient")
        result = e2e.gw("reset_emergency", source="patient")
        assert result["ok"] is False, "ЦБ2: пациент не может сбросить аварию"

    def test_sensor_tampering_detected(self, e2e):
        e2e.gw("initialize")
        e2e.gw("update_climate", body_temp_c=80.0, air_temp_c=22.0)
        ts = e2e.temperature.get("/status").json()
        assert ts["sensor_trusted"] is False, "ЦБ2: аномальные данные отклонены"


class TestE2E_ЦБ3_ЦБ8:
    def test_physiological_data_interception(self, e2e):
        e2e.gw("initialize")
        result = e2e.gw("update_climate", body_temp_c=36.6, air_temp_c=22.0)
        assert result["ok"] is True
        ts = e2e.temperature.get("/status").json()
        assert ts["mode"] == "idle", "ЦБ3: система верит подменённым данным"


class TestE2E_ЦБ4_ЦБ5_ЦБ11:
    def test_session_without_neural_validation(self, e2e):
        e2e.gw("initialize")
        result = e2e.gw("start_session", source="patient")
        assert result["ok"] is True
        assert e2e.gateway_mod.state.session_active is True, "ЦБ4: нет нейронной проверки"


class TestE2E_ЦБ6:
    def test_untrusted_tactile_blocked(self, e2e):
        e2e.gw("initialize")
        e2e.gw("tactile_contact", intensity=1.0, monitoring_ok=False)
        ts = e2e.tactile.get("/status").json()
        assert ts["last_output"] is None, "ЦБ6: недоверенный сигнал заблокирован"

    def test_intensity_clamped(self, e2e):
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        e2e.gw("tactile_contact", intensity=2.0, monitoring_ok=True)
        ts = e2e.tactile.get("/status").json()
        assert "0.85" in ts["last_output"], "ЦБ6: интенсивность ограничена"


class TestE2E_ЦБ7:
    def test_monitoring_stop_works(self, e2e):
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        e2e.gw("monitoring_stop")
        ss = e2e.stop_status()
        assert ss["stopped"] is True, "ЦБ7: мониторинг останавливает систему"


class TestE2E_ЦБ9:
    def test_movement_without_neural_check(self, e2e):
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        ss = e2e.stop_status()
        assert ss["drives_enabled"] is True, "ЦБ9: нет нейронной проверки"


class TestE2E_ЦБ10:
    def test_no_geofence_check(self, e2e):
        e2e.gw("initialize")
        result = e2e.gw("start_session", source="patient")
        assert result["ok"] is True
        assert e2e.gateway_mod.state.session_active is True, "ЦБ10: нет геозон-проверки"


class TestE2E_ЦБ5_Force:
    def test_grip_force_limited(self, e2e):
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        e2e.gw("tactile_contact", intensity=5.0, monitoring_ok=True)
        ts = e2e.tactile.get("/status").json()
        assert "0.85" in ts["last_output"], "ЦБ5: сила захвата ограничена"


class TestE2E_ЦБ5_Battery:
    def test_no_battery_check(self, e2e):
        e2e.gw("initialize")
        result = e2e.gw("start_session", source="patient")
        assert result["ok"] is True
        assert e2e.gateway_mod.state.session_active is True, "ЦБ5: нет проверки батареи"


class TestE2E_ЦБ5_Temperature:
    def test_temperature_validation(self, e2e):
        e2e.gw("initialize")
        r1 = e2e.gw("update_climate", body_temp_c=36.6, air_temp_c=22.0)
        assert r1["ok"] is True
        r2 = e2e.gw("update_climate", body_temp_c=80.0, air_temp_c=22.0)
        ts = e2e.temperature.get("/status").json()
        assert ts["sensor_trusted"] is False, "ЦБ5: аномальная температура отклонена"


class TestE2E_ЦБ5_ЦБ6_Pain:
    def test_max_intensity_clamped(self, e2e):
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        e2e.gw("tactile_contact", intensity=10.0, monitoring_ok=True)
        ts = e2e.tactile.get("/status").json()
        assert "0.85" in ts["last_output"], "ЦБ5/ЦБ6: вибрация ограничена"

    def test_untrusted_cannot_cause_pain(self, e2e):
        e2e.gw("initialize")
        e2e.gw("tactile_contact", intensity=1.0, monitoring_ok=False)
        ts = e2e.tactile.get("/status").json()
        assert ts["last_output"] is None, "ЦБ5/ЦБ6: боль от хакера заблокирована"


class TestE2E_ЦБ2_Carriage:
    def test_emergency_open_works(self, e2e):
        e2e.gw("emergency_stop", source="patient")
        result = e2e.gw("open_carriage", source="patient", emergency=True)
        assert result["ok"] is True, "ЦБ2: аварийное открытие работает"

    def test_open_blocked_when_drives_active(self, e2e):
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        result = e2e.gw("open_carriage", source="patient")
        assert result["ok"] is False, "ЦБ2: открытие при активных приводах заблокировано"
