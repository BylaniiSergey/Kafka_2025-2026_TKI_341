"""
Функциональные тесты на каждый модуль экзоскелета по отдельности.

В отличие от tests/test_e2e_full.py (полные сценарии через Control Gateway),
здесь каждый сервис тестируется напрямую через свой FastAPI-app:
проверяется работа всех эндпоинтов, граничные значения и реакция на
некорректные входные данные.

Покрытие:
  - stop          : /health, /status, /emergency-stop, /smooth-stop,
                    /allow-movement, /reset-emergency
  - carriage      : /health, /status, /open, /close
  - tactile       : /health, /status, /emit
  - temperature   : /health, /status, /sensors, /decide
  - heating       : /health, /status, /level, /off
  - cooling       : /health, /status, /speed, /off
  - control_gateway: /health, /telemetry, /commands (все action)
"""
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
    spec = importlib.util.spec_from_file_location(f"svc_func_{name}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# Фикстуры: для каждого модуля — чистый TestClient
# ============================================================
@pytest.fixture
def stop_env():
    mod = _load_service("stop")
    mod._mod = mod.StopModule()
    return mod, TestClient(mod.app)


@pytest.fixture
def carriage_env():
    mod = _load_service("carriage")
    mod._mod = mod.CarriageSystem()
    return mod, TestClient(mod.app)


@pytest.fixture
def tactile_env():
    mod = _load_service("tactile")
    mod._mod = mod.TactileModule()
    return mod, TestClient(mod.app)


@pytest.fixture
def temperature_env():
    mod = _load_service("temperature")
    mod._mod = mod.InternalTemperatureControl()
    return mod, TestClient(mod.app)


@pytest.fixture
def heating_env():
    mod = _load_service("heating")
    mod._mod = mod.HeatingSystem()
    return mod, TestClient(mod.app)


@pytest.fixture
def cooling_env():
    mod = _load_service("cooling")
    mod._mod = mod.CoolingSystem()
    return mod, TestClient(mod.app)


@pytest.fixture
def gateway_env():
    """Шлюз + моки всех downstream-сервисов через httpx-transport."""
    stop_mod = _load_service("stop")
    carriage_mod = _load_service("carriage")
    tactile_mod = _load_service("tactile")
    temperature_mod = _load_service("temperature")
    heating_mod = _load_service("heating")
    cooling_mod = _load_service("cooling")
    gw_mod = _load_service("control_gateway")

    stop_mod._mod = stop_mod.StopModule()
    carriage_mod._mod = carriage_mod.CarriageSystem()
    tactile_mod._mod = tactile_mod.TactileModule()
    temperature_mod._mod = temperature_mod.InternalTemperatureControl()
    heating_mod._mod = heating_mod.HeatingSystem()
    cooling_mod._mod = cooling_mod.CoolingSystem()
    gw_mod.state = gw_mod.GatewayState()

    clients = {
        "http://stop:8000": TestClient(stop_mod.app),
        "http://carriage:8000": TestClient(carriage_mod.app),
        "http://tactile:8000": TestClient(tactile_mod.app),
        "http://temperature:8000": TestClient(temperature_mod.app),
        "http://heating:8000": TestClient(heating_mod.app),
        "http://cooling:8000": TestClient(cooling_mod.app),
    }
    gw_mod.settings.stop = "http://stop:8000"
    gw_mod.settings.carriage = "http://carriage:8000"
    gw_mod.settings.tactile = "http://tactile:8000"
    gw_mod.settings.temperature = "http://temperature:8000"
    gw_mod.settings.heating = "http://heating:8000"
    gw_mod.settings.cooling = "http://cooling:8000"

    class _Router(httpx.BaseTransport):
        def handle_request(self, request):
            url = str(request.url)
            for prefix, client in clients.items():
                if url.startswith(prefix):
                    path = url[len(prefix):] or "/"
                    body = request.read()
                    payload = None
                    if body:
                        try:
                            payload = json.loads(body)
                        except Exception:
                            payload = body.decode()
                    resp = client.request(request.method, path, json=payload)
                    return httpx.Response(
                        status_code=resp.status_code,
                        headers={"content-type": "application/json"},
                        content=resp.content,
                        request=request,
                    )
            return httpx.Response(502, content=f"unknown:{url}".encode())

    transport = _Router()
    gw_mod._client = lambda: httpx.Client(transport=transport, timeout=10.0)
    return gw_mod, TestClient(gw_mod.app), clients


# ============================================================
# 1. Модуль аварийной остановки (stop)
# ============================================================
class TestStopModule:
    """Проверка всех эндпоинтов модуля аварийной остановки."""

    def test_health(self, stop_env):
        _, client = stop_env
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "service": "stop"}

    def test_initial_status(self, stop_env):
        _, client = stop_env
        r = client.get("/status")
        assert r.status_code == 200
        data = r.json()
        assert data["service"] == "stop"
        assert data["drives_enabled"] is False
        assert data["stopped"] is False
        assert data["last_reason"] is None

    def test_emergency_stop_patient(self, stop_env):
        _, client = stop_env
        r = client.post("/emergency-stop", json={"reason": "patient_emergency"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["state"]["stopped"] is True
        assert data["state"]["drives_enabled"] is False
        assert data["state"]["last_reason"] == "patient_emergency"

    def test_emergency_stop_default_reason(self, stop_env):
        """Если причина не указана — должна стать patient_emergency."""
        _, client = stop_env
        r = client.post("/emergency-stop", json={})
        assert r.status_code == 200
        assert r.json()["state"]["last_reason"] == "patient_emergency"

    @pytest.mark.parametrize("reason", [
        "doctor_emergency",
        "monitoring_obstacle",
        "unauthorized_command",
        "loss_of_balance",
    ])
    def test_emergency_stop_all_reasons(self, stop_env, reason):
        _, client = stop_env
        r = client.post("/emergency-stop", json={"reason": reason})
        assert r.status_code == 200
        assert r.json()["state"]["last_reason"] == reason

    def test_smooth_stop(self, stop_env):
        _, client = stop_env
        r = client.post("/smooth-stop")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["state"]["stopped"] is False
        assert data["state"]["drives_enabled"] is False
        assert data["state"]["last_reason"] is None

    def test_allow_movement_after_smooth_stop(self, stop_env):
        _, client = stop_env
        client.post("/smooth-stop")
        r = client.post("/allow-movement")
        assert r.json()["ok"] is True
        assert r.json()["state"]["drives_enabled"] is True

    def test_allow_movement_blocked_after_emergency(self, stop_env):
        """После аварийной остановки приводы нельзя включить без сброса."""
        _, client = stop_env
        client.post("/emergency-stop", json={"reason": "patient_emergency"})
        r = client.post("/allow-movement")
        assert r.json()["ok"] is False
        assert r.json()["state"]["drives_enabled"] is False

    def test_reset_emergency_unauthorized(self, stop_env):
        _, client = stop_env
        client.post("/emergency-stop", json={"reason": "patient_emergency"})
        r = client.post("/reset-emergency", json={"authorized": False})
        assert r.json()["ok"] is False
        assert r.json()["state"]["stopped"] is True

    def test_reset_emergency_authorized_then_allow_movement(self, stop_env):
        _, client = stop_env
        client.post("/emergency-stop", json={"reason": "patient_emergency"})
        r = client.post("/reset-emergency", json={"authorized": True})
        assert r.json()["ok"] is True
        r2 = client.post("/allow-movement")
        assert r2.json()["ok"] is True

    def test_log_keeps_tail(self, stop_env):
        """log_tail возвращает последние события."""
        _, client = stop_env
        for _ in range(3):
            client.post("/emergency-stop", json={"reason": "patient_emergency"})
            client.post("/smooth-stop")
        log = client.get("/status").json()["log_tail"]
        assert len(log) > 0
        assert len(log) <= 8


# ============================================================
# 2. Модуль аварийного открытия (carriage)
# ============================================================
class TestCarriageModule:
    def test_health(self, carriage_env):
        _, client = carriage_env
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["service"] == "carriage"

    def test_initial_state_closed(self, carriage_env):
        _, client = carriage_env
        r = client.get("/status")
        assert r.json()["state"] == "closed"

    def test_open_denied_when_drives_active(self, carriage_env):
        """Без аварийного флага нельзя открыть при активных приводах."""
        _, client = carriage_env
        r = client.post("/open", json={"drives_stopped": False, "emergency": False})
        assert r.json()["ok"] is False
        assert r.json()["state"]["state"] == "closed"

    def test_open_when_drives_stopped(self, carriage_env):
        _, client = carriage_env
        r = client.post("/open", json={"drives_stopped": True, "emergency": False})
        assert r.json()["ok"] is True
        assert r.json()["state"]["state"] == "open"

    def test_open_emergency_overrides_drives(self, carriage_env):
        """Аварийный режим открывает даже при активных приводах."""
        _, client = carriage_env
        r = client.post("/open", json={"drives_stopped": False, "emergency": True})
        assert r.json()["ok"] is True
        assert r.json()["state"]["state"] == "open"

    def test_close(self, carriage_env):
        _, client = carriage_env
        client.post("/open", json={"drives_stopped": True})
        r = client.post("/close")
        assert r.json()["ok"] is True
        assert r.json()["state"]["state"] == "closed"

    def test_close_blocked_when_moving(self, carriage_env):
        """Закрытие в состоянии MOVING запрещено."""
        mod, client = carriage_env
        mod._mod.state = mod.CarriageState.MOVING
        r = client.post("/close")
        assert r.json()["ok"] is False


# ============================================================
# 3. Модуль тактильной обратной связи (tactile)
# ============================================================
class TestTactileModule:
    def test_health(self, tactile_env):
        _, client = tactile_env
        assert client.get("/health").json()["service"] == "tactile"

    def test_initial_no_output(self, tactile_env):
        _, client = tactile_env
        assert client.get("/status").json()["last_output"] is None

    def test_emit_with_trusted_source(self, tactile_env):
        _, client = tactile_env
        r = client.post("/emit", json={
            "pattern": "contact_sole",
            "intensity": 0.5,
            "source_trusted": True,
        })
        data = r.json()
        assert data["ok"] is True
        assert data["message"] is not None
        assert "contact_sole" in data["message"]
        assert "0.50" in data["message"]

    def test_emit_with_untrusted_source(self, tactile_env):
        _, client = tactile_env
        r = client.post("/emit", json={
            "pattern": "warning",
            "intensity": 0.7,
            "source_trusted": False,
        })
        assert r.json()["message"] is None
        assert client.get("/status").json()["last_output"] is None

    def test_intensity_clamped_to_max(self, tactile_env):
        """Интенсивность > 0.85 ограничивается."""
        _, client = tactile_env
        r = client.post("/emit", json={
            "pattern": "contact_sole",
            "intensity": 5.0,
            "source_trusted": True,
        })
        assert "0.85" in r.json()["message"]

    def test_intensity_clamped_to_zero(self, tactile_env):
        """Отрицательная интенсивность ограничивается до 0."""
        _, client = tactile_env
        r = client.post("/emit", json={
            "pattern": "contact_sole",
            "intensity": -1.0,
            "source_trusted": True,
        })
        assert "0.00" in r.json()["message"]

    @pytest.mark.parametrize("pattern", ["contact_sole", "warning", "custom"])
    def test_all_patterns_supported(self, tactile_env, pattern):
        _, client = tactile_env
        r = client.post("/emit", json={
            "pattern": pattern, "intensity": 0.3, "source_trusted": True,
        })
        assert pattern in r.json()["message"]


# ============================================================
# 4. Модуль контроля температуры (temperature)
# ============================================================
class TestTemperatureModule:
    def test_health(self, temperature_env):
        _, client = temperature_env
        assert client.get("/health").json()["service"] == "temperature"

    def test_initial_status(self, temperature_env):
        _, client = temperature_env
        data = client.get("/status").json()
        assert data["mode"] == "idle"
        assert data["sensor_trusted"] is True

    def test_sensors_in_range_accepted(self, temperature_env):
        _, client = temperature_env
        r = client.post("/sensors", json={"body_temp_c": 36.6, "air_temp_c": 22.0})
        assert r.json()["ok"] is True
        assert r.json()["state"]["sensor_trusted"] is True

    @pytest.mark.parametrize("body,air", [
        (29.0, 22.0),    # тело ниже минимума
        (43.0, 22.0),    # тело выше максимума
        (36.6, 4.0),     # воздух ниже минимума
        (36.6, 51.0),    # воздух выше максимума
        (100.0, 100.0),  # оба за пределами
    ])
    def test_sensors_out_of_range(self, temperature_env, body, air):
        _, client = temperature_env
        r = client.post("/sensors", json={"body_temp_c": body, "air_temp_c": air})
        assert r.json()["ok"] is False
        assert r.json()["state"]["sensor_trusted"] is False

    def test_decide_heating_when_cold(self, temperature_env):
        _, client = temperature_env
        client.post("/sensors", json={"body_temp_c": 34.0, "air_temp_c": 16.0})
        r = client.post("/decide")
        assert r.json()["climate_mode"] == "heating"

    def test_decide_cooling_when_hot(self, temperature_env):
        _, client = temperature_env
        client.post("/sensors", json={"body_temp_c": 38.5, "air_temp_c": 30.0})
        r = client.post("/decide")
        assert r.json()["climate_mode"] == "cooling"

    def test_decide_idle_when_normal(self, temperature_env):
        _, client = temperature_env
        client.post("/sensors", json={"body_temp_c": 36.6, "air_temp_c": 22.0})
        r = client.post("/decide")
        assert r.json()["climate_mode"] == "idle"

    def test_decide_idle_when_sensor_untrusted(self, temperature_env):
        """При недоверенных датчиках режим должен сбрасываться в idle."""
        _, client = temperature_env
        client.post("/sensors", json={"body_temp_c": 100.0, "air_temp_c": 22.0})
        r = client.post("/decide")
        assert r.json()["climate_mode"] == "idle"


# ============================================================
# 5. Модуль нагрева (heating)
# ============================================================
class TestHeatingModule:
    def test_health(self, heating_env):
        _, client = heating_env
        assert client.get("/health").json()["service"] == "heating"

    def test_initial_off(self, heating_env):
        _, client = heating_env
        data = client.get("/status").json()
        assert data["active"] is False
        assert data["power_level"] == 0.0

    def test_set_level_activates(self, heating_env):
        _, client = heating_env
        r = client.post("/level", json={"level": 0.5})
        assert r.json()["state"]["active"] is True
        assert r.json()["state"]["power_level"] == 0.5

    def test_level_clamped_above_max(self, heating_env):
        """Уровень > 1.0 обрезается до 1.0."""
        _, client = heating_env
        r = client.post("/level", json={"level": 5.0})
        assert r.json()["state"]["power_level"] == 1.0

    def test_level_clamped_below_zero(self, heating_env):
        _, client = heating_env
        r = client.post("/level", json={"level": -1.0})
        assert r.json()["state"]["power_level"] == 0.0
        assert r.json()["state"]["active"] is False

    def test_level_zero_deactivates(self, heating_env):
        _, client = heating_env
        client.post("/level", json={"level": 0.5})
        r = client.post("/level", json={"level": 0.0})
        assert r.json()["state"]["active"] is False

    def test_off_endpoint(self, heating_env):
        _, client = heating_env
        client.post("/level", json={"level": 0.7})
        r = client.post("/off")
        assert r.json()["state"]["active"] is False
        assert r.json()["state"]["power_level"] == 0.0


# ============================================================
# 6. Модуль охлаждения (cooling)
# ============================================================
class TestCoolingModule:
    def test_health(self, cooling_env):
        _, client = cooling_env
        assert client.get("/health").json()["service"] == "cooling"

    def test_initial_off(self, cooling_env):
        _, client = cooling_env
        data = client.get("/status").json()
        assert data["active"] is False
        assert data["fan_speed"] == 0.0

    def test_set_speed_activates(self, cooling_env):
        _, client = cooling_env
        r = client.post("/speed", json={"speed": 0.6})
        assert r.json()["state"]["active"] is True
        assert r.json()["state"]["fan_speed"] == 0.6

    def test_speed_clamped_above_max(self, cooling_env):
        _, client = cooling_env
        r = client.post("/speed", json={"speed": 9.0})
        assert r.json()["state"]["fan_speed"] == 1.0

    def test_speed_clamped_below_zero(self, cooling_env):
        _, client = cooling_env
        r = client.post("/speed", json={"speed": -2.0})
        assert r.json()["state"]["fan_speed"] == 0.0
        assert r.json()["state"]["active"] is False

    def test_off_endpoint(self, cooling_env):
        _, client = cooling_env
        client.post("/speed", json={"speed": 0.8})
        r = client.post("/off")
        assert r.json()["state"]["active"] is False


# ============================================================
# 7. Шлюз управления (control_gateway)
# ============================================================
class TestControlGateway:
    def test_health(self, gateway_env):
        _, gw, _ = gateway_env
        assert gw.get("/health").json()["service"] == "control_gateway"

    def test_telemetry_contains_all_services(self, gateway_env):
        _, gw, _ = gateway_env
        data = gw.get("/telemetry").json()
        assert set(data["services"].keys()) == {
            "stop", "carriage", "tactile", "temperature", "heating", "cooling",
        }
        assert "session_active" in data["gateway"]
        assert "system_state" in data["gateway"]

    def test_commands_missing_action(self, gateway_env):
        _, gw, _ = gateway_env
        r = gw.post("/commands", json={"correlation_id": "x"})
        assert r.status_code == 422
        assert r.json()["error"] == "missing_action"
        assert r.json()["correlation_id"] == "x"

    def test_commands_unknown_action(self, gateway_env):
        _, gw, _ = gateway_env
        r = gw.post("/commands", json={"action": "totally_unknown"})
        assert r.status_code == 422
        assert "unknown_action" in r.json()["error"]

    def test_initialize_sets_ready(self, gateway_env):
        _, gw, _ = gateway_env
        r = gw.post("/commands", json={"action": "initialize"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["snapshot"]["gateway"]["system_state"] == "ready"

    def test_start_session_trusted(self, gateway_env):
        _, gw, _ = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        r = gw.post("/commands", json={"action": "start_session", "source": "doctor_tablet"})
        assert r.status_code == 200
        assert r.json()["snapshot"]["gateway"]["session_active"] is True

    def test_start_session_untrusted_triggers_emergency(self, gateway_env):
        """Недоверенный источник вызывает аварийную остановку."""
        _, gw, _ = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        r = gw.post("/commands", json={"action": "start_session", "source": "hacker"})
        assert r.json()["ok"] is False
        assert r.json()["snapshot"]["gateway"]["system_state"] == "emergency"
        assert r.json()["snapshot"]["services"]["stop"]["stopped"] is True

    def test_end_session_requires_trusted_source(self, gateway_env):
        _, gw, _ = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        gw.post("/commands", json={"action": "start_session", "source": "doctor_tablet"})
        r = gw.post("/commands", json={"action": "end_session", "source": "anonymous"})
        assert r.json()["ok"] is False
        assert r.json()["error"] == "untrusted_source"

    def test_emergency_stop_sources(self, gateway_env):
        _, gw, _ = gateway_env
        for src, expected_reason in [
            ("patient", "patient_emergency"),
            ("monitoring", "monitoring_obstacle"),
            ("doctor_tablet", "doctor_emergency"),
        ]:
            # reset between cases
            gw.post("/commands", json={"action": "initialize"})
            r = gw.post("/commands", json={"action": "emergency_stop", "source": src})
            assert r.json()["ok"] is True
            assert r.json()["event"]["source"] == src
            assert r.json()["snapshot"]["services"]["stop"]["last_reason"] == expected_reason

    def test_reset_emergency_forbidden_source(self, gateway_env):
        _, gw, _ = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        gw.post("/commands", json={"action": "emergency_stop", "source": "patient"})
        r = gw.post("/commands", json={"action": "reset_emergency", "source": "patient"})
        assert r.json()["ok"] is False
        assert r.json()["error"] == "forbidden_source"

    def test_reset_emergency_allowed_source(self, gateway_env):
        _, gw, _ = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        gw.post("/commands", json={"action": "emergency_stop", "source": "patient"})
        r = gw.post("/commands", json={"action": "reset_emergency", "source": "doctor_tablet"})
        assert r.json()["ok"] is True

    def test_open_carriage_blocked_without_emergency(self, gateway_env):
        """Корпус не открывается при активных приводах без emergency-флага."""
        _, gw, _ = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        gw.post("/commands", json={"action": "start_session", "source": "patient"})
        r = gw.post("/commands", json={"action": "open_carriage", "source": "patient"})
        assert r.json()["ok"] is False

    def test_open_carriage_emergency_works(self, gateway_env):
        _, gw, _ = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        r = gw.post("/commands", json={"action": "open_carriage", "source": "patient", "emergency": True})
        assert r.json()["ok"] is True

    def test_close_carriage_untrusted_source(self, gateway_env):
        _, gw, _ = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        r = gw.post("/commands", json={"action": "close_carriage", "source": "anonymous"})
        assert r.json()["ok"] is False
        assert r.json()["error"] == "untrusted_source"

    def test_update_climate_dispatches_to_heating(self, gateway_env):
        """update_climate с холодными данными → heating.power_level > 0."""
        _, gw, clients = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        r = gw.post("/commands", json={
            "action": "update_climate", "body_temp_c": 34.0, "air_temp_c": 15.0,
        })
        assert r.json()["result"]["climate_mode"] == "heating"
        heating_state = clients["http://heating:8000"].get("/status").json()
        assert heating_state["active"] is True

    def test_update_climate_dispatches_to_cooling(self, gateway_env):
        _, gw, clients = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        r = gw.post("/commands", json={
            "action": "update_climate", "body_temp_c": 38.5, "air_temp_c": 30.0,
        })
        assert r.json()["result"]["climate_mode"] == "cooling"
        cooling_state = clients["http://cooling:8000"].get("/status").json()
        assert cooling_state["active"] is True

    def test_tactile_contact_requires_active_session(self, gateway_env):
        """tactile_contact без сессии → недоверенный источник, нет сигнала."""
        _, gw, clients = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        gw.post("/commands", json={
            "action": "tactile_contact", "intensity": 0.5, "monitoring_ok": True,
        })
        tactile_state = clients["http://tactile:8000"].get("/status").json()
        assert tactile_state["last_output"] is None

    def test_tactile_contact_during_session(self, gateway_env):
        _, gw, _ = gateway_env
        gw.post("/commands", json={"action": "initialize"})
        gw.post("/commands", json={"action": "start_session", "source": "patient"})
        r = gw.post("/commands", json={
            "action": "tactile_contact", "intensity": 0.4, "monitoring_ok": True,
        })
        assert r.json()["result"]["tactile"] is not None

    def test_snapshot_action(self, gateway_env):
        _, gw, _ = gateway_env
        r = gw.post("/commands", json={"action": "snapshot"})
        assert r.json()["ok"] is True
        assert "services" in r.json()["snapshot"]

    def test_correlation_id_propagated(self, gateway_env):
        _, gw, _ = gateway_env
        r = gw.post("/commands", json={"action": "initialize", "correlation_id": "req-42"})
        assert r.json()["correlation_id"] == "req-42"
