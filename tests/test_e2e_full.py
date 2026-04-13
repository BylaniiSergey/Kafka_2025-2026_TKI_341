"""
E2E-тесты нормальной работы системы экзоскелета.
Полные сценарии через Control Gateway (POST /commands).
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
                return httpx.Response(502, content=f"unknown: {url}".encode())

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

    def telemetry(self) -> dict:
        r = self.gateway.get("/telemetry")
        return r.json()


@pytest.fixture
def e2e():
    env = _E2EEnv()
    env.reset()
    yield env


# ============================================================
# Полная сессия реабилитации
# ============================================================
class TestE2E_FullSession:
    def test_full_rehab_session(self, e2e):
        """Инициализация → сессия → климат → тактиль → конец сессии."""
        # 1. Инициализация
        r = e2e.gw("initialize", source="operator")
        assert r["ok"] is True
        assert r["result"]["initialized"] is True

        # 2. Начало сессии
        r = e2e.gw("start_session", source="doctor_tablet")
        assert r["ok"] is True
        assert r["snapshot"]["gateway"]["session_active"] is True

        # 3. Климат-контроль (норма)
        r = e2e.gw("update_climate", body_temp_c=36.6, air_temp_c=22.0)
        assert r["ok"] is True
        assert r["result"]["climate_mode"] == "idle"

        # 4. Тактильная обратная связь
        r = e2e.gw("tactile_contact", intensity=0.5, monitoring_ok=True)
        assert r["ok"] is True

        # 5. Конец сессии
        r = e2e.gw("end_session", source="doctor_tablet")
        assert r["ok"] is True
        assert r["snapshot"]["gateway"]["session_active"] is False

    def test_emergency_stop_and_reset(self, e2e):
        """Аварийная остановка → сброс → повторный запуск."""
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")

        # Аварийная остановка
        r = e2e.gw("emergency_stop", source="patient")
        assert r["ok"] is True
        assert r["snapshot"]["services"]["stop"]["stopped"] is True
        assert r["snapshot"]["services"]["stop"]["drives_enabled"] is False

        # Попытка сессии в аварийном режиме → отказ
        r = e2e.gw("start_session", source="doctor_tablet")
        assert r["ok"] is False

        # Сброс от врача
        r = e2e.gw("reset_emergency", source="doctor_tablet")
        assert r["ok"] is True

        # Повторный запуск
        r = e2e.gw("start_session", source="doctor_tablet")
        assert r["ok"] is True

    def test_carriage_safety(self, e2e):
        """Корпус: нельзя открыть при активных приводах."""
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")

        # Открытие при активных приводах → отказ
        r = e2e.gw("open_carriage", source="patient", emergency=False)
        assert r["ok"] is False

        # Аварийная остановка → приводы остановлены
        e2e.gw("emergency_stop", source="patient")

        # Аварийное открытие → работает
        r = e2e.gw("open_carriage", source="patient", emergency=True)
        assert r["ok"] is True

        # Закрытие
        r = e2e.gw("close_carriage", source="doctor_tablet")
        assert r["ok"] is True


# ============================================================
# Климат-контроль
# ============================================================
class TestE2E_Climate:
    def test_heating_activation(self, e2e):
        """Холодно → система включает нагрев."""
        e2e.gw("initialize")
        r = e2e.gw("update_climate", body_temp_c=34.0, air_temp_c=16.0)
        assert r["ok"] is True
        assert r["result"]["climate_mode"] == "heating"

        ts = e2e.temperature.get("/status").json()
        assert ts["sensor_trusted"] is True

    def test_cooling_activation(self, e2e):
        """Жарко → система включает охлаждение."""
        e2e.gw("initialize")
        r = e2e.gw("update_climate", body_temp_c=38.5, air_temp_c=30.0)
        assert r["ok"] is True
        assert r["result"]["climate_mode"] == "cooling"

    def test_idle_climate(self, e2e):
        """Нормальная температура → климат выключен."""
        e2e.gw("initialize")
        r = e2e.gw("update_climate", body_temp_c=36.6, air_temp_c=22.0)
        assert r["ok"] is True
        assert r["result"]["climate_mode"] == "idle"

    def test_sensor_out_of_range(self, e2e):
        """Датчик за пределами диапазона → данные не доверяют."""
        e2e.gw("initialize")
        r = e2e.gw("update_climate", body_temp_c=80.0, air_temp_c=22.0)
        assert r["ok"] is True
        ts = e2e.temperature.get("/status").json()
        assert ts["sensor_trusted"] is False


# ============================================================
# Телеметрия
# ============================================================
class TestE2E_Telemetry:
    def test_telemetry_aggregation(self, e2e):
        """Телеметрия собирает данные со всех сервисов."""
        r = e2e.telemetry()

        expected = ["stop", "carriage", "tactile", "temperature", "heating", "cooling"]
        for svc in expected:
            assert svc in r["services"], f"Сервис {svc} отсутствует в телеметрии"

        assert "gateway" in r
        assert "session_active" in r["gateway"]
        assert "system_state" in r["gateway"]

    def test_telemetry_after_session(self, e2e):
        """Телеметрия после сессии показывает correct состояние."""
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")

        r = e2e.telemetry()
        assert r["gateway"]["session_active"] is True
        assert r["gateway"]["system_state"] == "session_active"

        e2e.gw("end_session", source="doctor_tablet")
        r = e2e.telemetry()
        assert r["gateway"]["session_active"] is False


# ============================================================
# Тактильная обратная связь
# ============================================================
class TestE2E_Tactile:
    def test_tactile_with_session(self, e2e):
        """Тактильный сигнал во время сессии → доставляется."""
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        r = e2e.gw("tactile_contact", intensity=0.5, monitoring_ok=True)
        assert r["ok"] is True
        assert r["result"]["tactile"] is not None

    def test_tactile_without_session(self, e2e):
        """Тактильный сигнал без сессии → отклоняется."""
        e2e.gw("initialize")
        r = e2e.gw("tactile_contact", intensity=0.5, monitoring_ok=False)
        ts = e2e.tactile.get("/status").json()
        assert ts["last_output"] is None

    def test_tactile_intensity_limiting(self, e2e):
        """Интенсивность ограничена max_intensity=0.85."""
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        e2e.gw("tactile_contact", intensity=5.0, monitoring_ok=True)
        ts = e2e.tactile.get("/status").json()
        assert "0.85" in ts["last_output"]


# ============================================================
# Остановки
# ============================================================
class TestE2E_Stops:
    def test_patient_emergency_stop(self, e2e):
        """Экстренная остановка пациентом."""
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        r = e2e.gw("emergency_stop", source="patient")
        assert r["ok"] is True
        assert r["event"]["type"] == "emergency_stop"
        assert r["event"]["source"] == "patient"

    def test_monitoring_stop(self, e2e):
        """Остановка по команде мониторинга."""
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        r = e2e.gw("monitoring_stop")
        assert r["ok"] is True
        assert r["event"]["type"] == "monitoring_stop"
        ss = e2e.stop_status()
        assert ss["stopped"] is True

    def test_smooth_stop(self, e2e):
        """Плавная остановка при конце сессии."""
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        r = e2e.gw("end_session", source="doctor_tablet")
        assert r["ok"] is True
        ss = e2e.stop_status()
        assert ss["drives_enabled"] is False


# ============================================================
# Логирование
# ============================================================
class TestE2E_Logging:
    def test_stop_service_logging(self, e2e):
        """Stop-сервис записывает события в лог."""
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        e2e.gw("emergency_stop", source="patient")

        ss = e2e.stop_status()
        assert "log_tail" in ss
        assert len(ss["log_tail"]) > 0

    def test_temperature_service_logging(self, e2e):
        """Temperature-сервис записывает события."""
        e2e.gw("initialize")
        e2e.gw("update_climate", body_temp_c=36.6, air_temp_c=22.0)

        ts = e2e.temperature.get("/status").json()
        assert "log_tail" in ts
        assert len(ts["log_tail"]) > 0

    def test_tactile_service_logging(self, e2e):
        """Tactile-сервис записывает события."""
        e2e.gw("initialize")
        e2e.gw("start_session", source="patient")
        e2e.gw("tactile_contact", intensity=0.5, monitoring_ok=True)

        ts = e2e.tactile.get("/status").json()
        assert "history_tail" in ts
        assert len(ts["history_tail"]) > 0
