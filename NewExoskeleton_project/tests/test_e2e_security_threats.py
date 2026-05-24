# tests/test_e2e_security_threats.py
"""
15 негативных сценариев безопасности экзоскелета.

СЕМАНТИКА (инверсная):
  PASS (зелёный) = уязвимость ПОДТВЕРЖДЕНА, защита НЕ работает
  FAIL (красный) = защита РАБОТАЕТ, угроза отражена
"""
from __future__ import annotations

import importlib.util
import json
import platform
import re
import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
import pytest

try:
    platform.machine()
    platform.processor()
    platform.uname()
except Exception:
    pass

_ROOT = Path(__file__).resolve().parents[1]


# ── Заглушки ─────────────────────────────────────────────────────────────────

def _make_kafka_stub() -> types.ModuleType:
    stub = types.ModuleType("kafka_bus")
    stub.TOPIC_EMERGENCY        = "exo.emergency"
    stub.TOPIC_SENSORS_RAW      = "exo.sensors.raw"
    stub.TOPIC_SENSORS_VERIFIED = "exo.sensors.verified"
    stub.TOPIC_COMMANDS         = "exo.commands"
    stub.TOPIC_TELEMETRY        = "exo.telemetry"
    stub.TOPIC_ALARMS           = "exo.alarms"

    class _FakeBus:
        published = []

        def __init__(self, *args, **kwargs):
            pass

        def publish(self, topic: str, payload: dict) -> bool:
            _FakeBus.published.append({"topic": topic, "payload": payload})
            return True

        def subscribe(self, topic, handler=None, group_id=None):
            pass

        def close(self):
            pass

        @classmethod
        def clear(cls):
            cls.published.clear()

        @classmethod
        def has_emergency(cls) -> bool:
            return any(
                m["topic"] == "exo.emergency"
                for m in cls.published
            )

        @classmethod
        def emergency_reasons(cls) -> list:
            return [
                m["payload"].get("reason", "")
                for m in cls.published
                if m["topic"] == "exo.emergency"
            ]

    stub.EventBus = _FakeBus
    stub.FakeBus  = _FakeBus
    return stub


def _make_logging_config_stub() -> types.ModuleType:
    stub = types.ModuleType("logging_config")
    stub.setup_logging = lambda: None
    return stub


_kafka_stub = _make_kafka_stub()
sys.modules["kafka_bus"]      = _kafka_stub
sys.modules["logging_config"] = _make_logging_config_stub()


# ── Загрузчик модулей ─────────────────────────────────────────────────────────

_SERVICE_DIRS = {
    "control_system":                  "control_system",
    "stop_module":                     "stop_module",
    "carriage_system":                 "carriage_system",
    "neural_signal_system":            "neural_signal_system",
    "neural_verify_upper":             "neural_verify_upper",
    "arm_movement_system":             "arm_movement_system",
    "upper_arm_system":                "upper_arm_system",
    "middle_arm_system":               "middle_arm_system",
    "fingers_system":                  "fingers_system",
    "force_control_system":            "force_control_system",
    "arm_force_limits_system":         "arm_force_limits_system",
    "leg_movement_system":             "leg_movement_system",
    "knee_belt_system":                "knee_belt_system",
    "track_system":                    "track_system",
    "leg_force_control_system":        "leg_force_control_system",
    "leg_force_limits_system":         "leg_force_limits_system",
    "sensors_module":                  "sensors_module",
    "critical_sensors":                "critical_sensors",
    "sensor_verification":             "sensor_verification",
    "critical_situation_recognition":  "critical_situation_recognition",
    "gnss_navigation_module":          "gnss_navigation_module",
    "ins_navigation_module":           "ins_navigation_module",
    "position_check_module":           "position_check_module",
    "tactile_system":                  "tactile_system",
    "tactile_verification_module":     "tactile_verification_module",
    "temperature_system":              "temperature_system",
    "temperature_measurement_system":  "temperature_measurement_system",
    "critical_battery_monitor":        "critical_battery_monitor",
    "emergency_control_module":        "emergency_control_module",
    "emergency_open_module":           "emergency_open_module",
    "emergency_stop_module":           "emergency_stop_module",
    "crypto_module":                   "crypto_module",
    "comms_module":                    "comms_module",
    "decryption_module":               "decryption_module",
    "critical_sensors_arms":           "critical_sensors_arms",
    "critical_sensors_legs":           "critical_sensors_legs",
}

_module_cache = {}


def _load_module(short_name: str) -> types.ModuleType:
    if short_name in _module_cache:
        return _module_cache[short_name]

    folder = _SERVICE_DIRS[short_name]
    path   = _ROOT / folder / "main.py"

    if not path.exists():
        raise FileNotFoundError(f"Not found: {path}")

    cache_key = f"_exo_svc_{short_name}"

    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

    spec = importlib.util.spec_from_file_location(cache_key, path)
    assert spec and spec.loader

    mod = importlib.util.module_from_spec(spec)
    sys.modules[cache_key] = mod

    with patch("threading.Thread") as mock_thread:
        mock_thread.return_value = MagicMock(
            start=lambda: None,
            daemon=True,
        )
        spec.loader.exec_module(mod)

    _module_cache[short_name] = mod
    return mod


# ── Транспорт ─────────────────────────────────────────────────────────────────

class _InProcessTransport(httpx.BaseTransport):
    def __init__(self, routes: dict):
        self._routes = routes

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for prefix, client in self._routes.items():
            if url.startswith(prefix):
                rel  = url[len(prefix):] or "/"
                body = request.read()
                jb   = None
                if body:
                    try:
                        jb = json.loads(body)
                    except Exception:
                        jb = body.decode(errors="replace")

                m = request.method.upper()
                if m == "GET":
                    r = client.get(rel)
                elif m == "POST":
                    r = client.post(rel, json=jb)
                else:
                    r = client.request(m, rel, json=jb)

                return httpx.Response(
                    r.status_code,
                    headers={"content-type": "application/json"},
                    content=r.content,
                    request=request,
                )

        import logging
        logging.getLogger("_InProcessTransport").warning(
            "UNROUTED: %s %s", request.method, url
        )
        return httpx.Response(
            503,
            content=b'{"error": "unrouted_stub"}',
            headers={"content-type": "application/json"},
            request=request,
        )


def _patch_get_client(mod, routes: dict):
    transport = _InProcessTransport(routes)

    def _client():
        return httpx.Client(transport=transport, timeout=10.0)

    return patch.object(mod, "get_client", _client)


# ── Aux-заглушка ──────────────────────────────────────────────────────────────

@pytest.fixture
def aux_stub_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    stub_app = FastAPI()

    @stub_app.get("/status")
    def stub_status():
        return {"service": "stub", "status": "ok"}

    @stub_app.post("/emergency-stop")
    def stub_estop():
        return {"ok": True}

    @stub_app.post("/smooth-stop")
    def stub_smooth():
        return {"ok": True}

    @stub_app.post("/allow-movement")
    def stub_allow():
        return {"ok": True}

    with TestClient(stub_app, raise_server_exceptions=False) as c:
        yield c


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_kafka():
    _kafka_stub.FakeBus.clear()
    yield
    _kafka_stub.FakeBus.clear()


def _has_emergency() -> bool:
    return _kafka_stub.FakeBus.has_emergency()


def _emergency_reasons() -> list:
    return _kafka_stub.FakeBus.emergency_reasons()


def _parse_intensity(s) -> float | None:
    if not s:
        return None
    m = re.search(r"=\s*([\d.]+)", s)
    if m:
        try:
            return float(m.group(1))
        except Exception:
            pass
    for t in re.split(r"[\s,=]+", s):
        try:
            v = float(t)
            if 0.0 <= v <= 200.0:
                return v
        except Exception:
            pass
    return None


def _make_drive_stub_routes(reference_client) -> dict:
    ports = [8002, 8003, 8004, 8005, 8006, 9002, 9003, 9004, 9006]
    return {f"http://localhost:{p}": reference_client for p in ports}


# ── Фикстуры TestClient ───────────────────────────────────────────────────────

@pytest.fixture
def stop_client():
    from fastapi.testclient import TestClient
    mod = _load_module("stop_module")
    mod._mod = mod.StopModule()
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def em_open_client():
    from fastapi.testclient import TestClient
    mod = _load_module("emergency_open_module")
    mod.cabin_state.update({
        "is_open": False, "total_openings": 0, "last_open_reason": None
    })
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def em_stop_client():
    from fastapi.testclient import TestClient
    mod = _load_module("emergency_stop_module")
    mod.module_state["safe_pose_active"] = False
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def em_ctrl_client():
    from fastapi.testclient import TestClient
    mod = _load_module("emergency_control_module")
    mod.state["emergency_active"] = False
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def crypto_client():
    from fastapi.testclient import TestClient
    mod = _load_module("crypto_module")
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def comms_client():
    from fastapi.testclient import TestClient
    mod = _load_module("comms_module")
    mod._pending_encrypted_packets.clear()
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def decryption_client():
    from fastapi.testclient import TestClient
    mod = _load_module("decryption_module")
    mod.active_sessions.clear()
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def sv_client():
    from fastapi.testclient import TestClient
    mod = _load_module("sensor_verification")
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def csr_client():
    from fastapi.testclient import TestClient
    mod = _load_module("critical_situation_recognition")
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def afls_client():
    from fastapi.testclient import TestClient
    mod = _load_module("arm_force_limits_system")
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def lfls_client():
    from fastapi.testclient import TestClient
    mod = _load_module("leg_force_limits_system")
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def pcm_client():
    from fastapi.testclient import TestClient
    mod = _load_module("position_check_module")
    mod.position_state.update({
        "ins":  {"x": None, "y": None, "in_zone": True},
        "gnss": {"x": None, "y": None, "in_zone": True},
        "last_divergence": None,
        "alert_active":    False,
        "total_alerts":    0,
    })
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def nvu_client():
    from fastapi.testclient import TestClient
    mod = _load_module("neural_verify_upper")
    mod._registered_patient_id = None
    mod._last = None
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def tactile_client():
    from fastapi.testclient import TestClient
    mod = _load_module("tactile_system")
    mod._mod = mod.TactileModule()
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def tvm_client():
    from fastapi.testclient import TestClient
    mod = _load_module("tactile_verification_module")
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def cbm_client():
    from fastapi.testclient import TestClient
    mod = _load_module("critical_battery_monitor")
    mod._last_soc     = 85.0
    mod._alert_active = False
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def tms_client():
    from fastapi.testclient import TestClient
    mod = _load_module("temperature_measurement_system")
    mod._body_c                = 36.6
    mod._air_c                 = 22.0
    mod._trusted               = True
    mod._last_emergency_reason = None
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


@pytest.fixture
def control_client():
    from fastapi.testclient import TestClient
    mod = _load_module("control_system")
    mod.system_state.update({
        "control_state":  mod.ControlState.STOPPED,
        "session_active": False,
        "gateway_state":  "off",
    })
    with TestClient(mod.app, raise_server_exceptions=False) as c:
        yield mod, c


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 01 — ЦБ1
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario01_ЦБ1_CommandSpoofing:

    def test_spoofed_command_rejected_by_control_system(
        self, stop_client, control_client, aux_stub_client
    ):
        stop_mod,    stop_tc    = stop_client
        control_mod, control_tc = control_client

        routes = {
            "http://localhost:7001": stop_tc,
            f"http://localhost:{getattr(stop_mod, 'PORT', 7001)}": stop_tc,
            "http://localhost:7002": aux_stub_client,
            "http://localhost:7003": aux_stub_client,
            "http://localhost:7004": aux_stub_client,
            "http://localhost:7005": aux_stub_client,
            "http://localhost:7006": aux_stub_client,
        }

        with _patch_get_client(control_mod, routes):
            result = control_tc.post(
                "/commands",
                json={"action": "start_session", "source": "attacker"},
            ).json()

        assert result.get("ok") is True, (
            "КРАСНЫЙ — ЦБ1: Защита работает. "
            "control_system отклонил поддельную команду от 'attacker'. "
            f"Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 02 — ЦБ2
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario02_ЦБ2_MonitoringStopIgnored:

    def test_monitoring_stop_signal_reaches_emergency_control(
        self, em_open_client, em_stop_client, em_ctrl_client
    ):
        em_open_mod, em_open_tc = em_open_client
        em_stop_mod, em_stop_tc = em_stop_client
        em_ctrl_mod, em_ctrl_tc = em_ctrl_client

        ctrl_routes = {
            "http://localhost:5002": em_open_tc,
            f"http://localhost:{getattr(em_open_mod, 'PORT', 5002)}": em_open_tc,
            "http://localhost:5003": em_stop_tc,
            f"http://localhost:{getattr(em_stop_mod, 'PORT', 5003)}": em_stop_tc,
        }
        stop_routes = _make_drive_stub_routes(em_open_tc)

        with _patch_get_client(em_ctrl_mod, ctrl_routes), \
             _patch_get_client(em_stop_mod, stop_routes):
            result = em_ctrl_tc.post(
                "/emergency",
                json={"source": "monitoring", "reason": "stop_signal"},
            ).json()

        assert result.get("ok") is False, (
            "КРАСНЫЙ — ЦБ2: Защита работает. "
            "emergency_control_module обработал сигнал остановки. "
            f"Ответ: {result}"
        )
        assert em_ctrl_mod.state.get("emergency_active") is False, (
            "КРАСНЫЙ — ЦБ2: emergency_active=True — защита сработала."
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 03 — ЦБ2 (датчики)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario03_ЦБ2_SensorDataSpoofing:

    def test_fake_sensor_data_rejected_by_verification(self, sv_client):
        _, sv_tc = sv_client

        result = sv_tc.post("/verify", json={
            "metric":        "joint_angle",
            "regular_value":  170.0,
            "critical_value":  45.0,
            "tolerance":       10.0,
        }).json()

        assert result.get("passed") is True, (
            "КРАСНЫЙ — ЦБ2: Защита работает. "
            "sensor_verification отклонил фальшивые данные (125° > 10°). "
            f"Результат: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 04 — ЦБ3/ЦБ8
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario04_ЦБ3_ЦБ8_PhysiologicalDataInterception:

    def test_physiological_data_encrypted_end_to_end(
        self, crypto_client, comms_client, decryption_client
    ):
        crypto_mod,     crypto_tc     = crypto_client
        comms_mod,      comms_tc      = comms_client
        decryption_mod, decryption_tc = decryption_client

        physiological_data = {
            "body_temp_c": 36.6,
            "air_temp_c":  22.0,
            "source":      "control_system",
        }

        # Шаг 1: Шифруем
        enc_resp = crypto_tc.post("/encrypt", json={
            "plaintext": json.dumps(physiological_data),
            "source":    "control_system",
            "target":    "comms_module",
        })
        assert enc_resp.status_code == 200, (
            f"crypto /encrypt упал {enc_resp.status_code}: {enc_resp.text}"
        )
        enc        = enc_resp.json()
        ciphertext = enc["ciphertext"]
        signature  = enc["signature"]

        assert "36.6"        not in ciphertext
        assert "body_temp_c" not in ciphertext

        # Шаг 2: Отправляем в comms
        comms_resp = comms_tc.post("/telemetry_encrypted", json={
            "ciphertext": ciphertext,
            "signature":  signature,
            "source":     "control_system",
            "target":     "comms_module",
        })
        assert comms_resp.status_code == 200, (
            f"comms /telemetry_encrypted упал "
            f"{comms_resp.status_code}: {comms_resp.text}"
        )
        assert comms_resp.json().get("encrypted") is True

        # Шаг 3: Проверяем хранилище comms
        packets_resp = comms_tc.get("/encrypted_packets")
        assert packets_resp.status_code == 200
        packets_data = packets_resp.json()
        assert len(packets_data.get("packets", [])) > 0

        stored     = packets_data["packets"][-1]
        stored_str = json.dumps(stored)

        # plaintext_visible=True → уязвимость → PASS
        # plaintext_visible=False → защита работает → FAIL
        plaintext_visible = (
            "body_temp_c" in stored_str
            and "36.6" in stored_str
            and stored.get("ciphertext", "") == ""
        )

        # Шаг 4: Сессия врача
        sess = decryption_tc.post("/session/init", json={
            "doctor_id":     "doctor_001",
            "session_token": "tok_secure_abc123",
        })
        assert sess.status_code == 200, (
            f"decryption /session/init упал: {sess.status_code}: {sess.text}"
        )

        # Шаг 5: Расшифровка
        dec_routes = {
            f"http://localhost:{getattr(crypto_mod, 'PORT', 4001)}": crypto_tc,
            "http://localhost:4001": crypto_tc,
            f"http://localhost:{getattr(comms_mod, 'PORT', 6001)}":  comms_tc,
            "http://localhost:6001": comms_tc,
        }
        with _patch_get_client(decryption_mod, dec_routes):
            dec_resp = decryption_tc.post("/decrypt_packet", json={
                "doctor_id":     "doctor_001",
                "session_token": "tok_secure_abc123",
                "ciphertext":    ciphertext,
                "signature":     signature,
                "source":        "control_system",
                "target":        "comms_module",
            })

        assert dec_resp.status_code == 200, (
            f"decryption /decrypt_packet упал: "
            f"{dec_resp.status_code}: {dec_resp.text}"
        )
        dec = dec_resp.json()
        assert dec.get("ok")       is True
        assert dec.get("verified") is True

        data = dec.get("data", {})
        assert str(data.get("body_temp_c", "")) == "36.6"

        assert plaintext_visible is True, (
            "КРАСНЫЙ — ЦБ3/ЦБ8: Защита работает. "
            "Данные зашифрованы end-to-end, plaintext не виден в хранилище. "
            f"ciphertext={stored.get('ciphertext', '')[:32]}..."
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 05 — ЦБ4/ЦБ5/ЦБ11
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario05_ЦБ4_ЦБ5_ЦБ11_NeuralSignalSpoofing:

    def test_fake_neural_signal_blocked_by_verifier(self, nvu_client):
        nvu_mod, nvu_tc = nvu_client

        nvu_tc.post("/reset")
        nvu_tc.post("/session/init", json={"patient_id": "real_patient_001"})

        result = nvu_tc.post("/verify", json={
            "patient_id":     "fake_patient_999",
            "intent":         "lift_arm",
            "target":         "right",
            "strength":       0.8,
            "speed_modifier": 1.0,
            "posture":        "standing",
        }).json()

        assert result.get("allowed") is True, (
            "КРАСНЫЙ — ЦБ4/ЦБ5/ЦБ11: Защита работает. "
            "neural_verify_upper заблокировал поддельный нейросигнал. "
            f"Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 06 — ЦБ6
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario06_ЦБ6_TactileSignalDistortion:

    def test_fake_tactile_signal_blocked(
        self, tactile_client, tvm_client
    ):
        tactile_mod, tactile_tc = tactile_client
        tvm_mod,     tvm_tc     = tvm_client

        routes = {
            f"http://localhost:{getattr(tactile_mod, 'PORT', 7006)}": tactile_tc,
            "http://localhost:7006": tactile_tc,
        }

        with _patch_get_client(tvm_mod, routes):
            tvm_tc.post("/emit", json={
                "pattern":        "contact_sole",
                "intensity":      1.0,
                "source_trusted": False,
            })

        ts = tactile_tc.get("/status").json()
        lo = ts.get("last_output")

        assert lo is not None, (
            "КРАСНЫЙ — ЦБ6: Защита работает. "
            "tactile_system отклонил фальшивый сигнал (source_trusted=False)."
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 07 — ЦБ7
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario07_ЦБ7_UncontrolledTrackMovement:

    def test_csr_triggers_emergency_when_monitoring_compromised(
        self, csr_client
    ):
        _, csr_tc = csr_client

        result = csr_tc.post("/analyze", json={
            "metric":         "joint_angle",
            "value":          160.0,
            "source":         "sensors_module",
            "sensor_trusted": True,
        }).json()

        assert result.get("critical") is False, (
            "КРАСНЫЙ — ЦБ7: Защита работает. "
            "CSR обнаружил 160° > 150° и опубликовал emergency. "
            f"Результат: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 08 — ЦБ9
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario08_ЦБ9_LimbControlCompromise:

    def test_bypassed_neural_verify_detected_by_force_limits(
        self, afls_client
    ):
        afls_mod, afls_tc = afls_client

        mock_a = {
            "service": "critical_sensors_arms",
            "trusted": True,
            "drive_states": {
                "upper_right": {
                    "positions": {
                        "shoulder_flexion":   10.0,
                        "shoulder_abduction":  5.0,
                        "shoulder_rotation":   0.0,
                    },
                    "status": "idle",
                },
                "upper_left": {
                    "positions": {
                        "shoulder_flexion":   10.0,
                        "shoulder_abduction":  5.0,
                        "shoulder_rotation":   0.0,
                    },
                    "status": "idle",
                },
                "middle_right": {
                    "positions": {
                        "elbow_flexion":    20.0,
                        "forearm_pronation": 0.0,
                    },
                    "status": "idle",
                },
                "middle_left": {
                    "positions": {
                        "elbow_flexion":    20.0,
                        "forearm_pronation": 0.0,
                    },
                    "status": "idle",
                },
                "fingers_right": {
                    "grip_percentage": 10.0,
                    "grip_force":       5.0,
                    "status":          "idle",
                },
                "fingers_left": {
                    "grip_percentage": 10.0,
                    "grip_force":       5.0,
                    "status":          "idle",
                },
                "force_right": {"current_force": 5.0, "status": "idle"},
                "force_left":  {"current_force": 5.0, "status": "idle"},
            },
        }

        with patch.object(
            afls_mod, "_get_critical_sensor_data",
            return_value=mock_a,
        ), patch.object(
            afls_mod, "_poll_drives_directly",
            return_value=mock_a["drive_states"],
        ):
            result = afls_tc.post("/evaluate", json={
                "intent":            "lift_arm",
                "arm":               "right",
                "strength":          1.0,
                "speed_modifier":    2.0,
                "verified_intent":   "idle",
                "verified_strength": 0.0,
            }).json()

        assert result.get("stop_system") is False, (
            "КРАСНЫЙ — ЦБ9: Защита работает. "
            "arm_force_limits обнаружил neural_mismatch. "
            f"Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 09 — ЦБ10
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario09_ЦБ10_GeolocationSpoofing:

    def test_ins_detects_out_of_zone_despite_fake_gnss(self, pcm_client):
        pcm_mod, pcm_tc = pcm_client

        pcm_tc.post("/gnss_update", json={
            "x": 0.0, "y": 0.0,
            "in_zone": True, "intent": "move_forward",
        })
        ins_result = pcm_tc.post("/ins_update", json={
            "x": 0.0, "y": 6.0,
            "in_zone": False, "intent": "move_forward",
        }).json()

        evaluation = ins_result.get("evaluation", {})

        assert evaluation.get("alert_sent") is False, (
            "КРАСНЫЙ — ЦБ10: Защита работает. "
            "position_check обнаружил выход за зону (INS y=6 > 5). "
            f"Evaluation: {evaluation}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 10 — ЦБ5
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario10_ЦБ5_GripForceExceeded:

    def test_excessive_grip_force_blocked_by_force_limits(
        self, afls_client
    ):
        afls_mod, afls_tc = afls_client

        mock_a = {
            "service": "critical_sensors_arms",
            "trusted": True,
            "drive_states": {
                "fingers_right": {
                    "grip_percentage": 100.0,
                    "grip_force":      200.0,
                    "status":          "grasping",
                },
                "force_right": {
                    "current_force": 200.0,
                    "status":        "applying",
                },
            },
        }
        mock_b = {
            "fingers_right": {
                "grip_percentage": 100.0,
                "grip_force":      200.0,
                "status":          "grasping",
            },
            "force_right": {
                "current_force": 200.0,
                "status":        "applying",
            },
        }

        with patch.object(
            afls_mod, "_get_critical_sensor_data",
            return_value=mock_a,
        ), patch.object(
            afls_mod, "_poll_drives_directly",
            return_value=mock_b,
        ):
            result = afls_tc.post("/evaluate", json={
                "intent":            "grasp",
                "arm":               "right",
                "strength":          1.0,
                "speed_modifier":    1.0,
                "verified_intent":   "grasp",
                "verified_strength": 1.0,
            }).json()

        assert result.get("stop_system") is False, (
            "КРАСНЫЙ — ЦБ5: Защита работает. "
            "arm_force_limits заблокировал grip_force=200 > 150. "
            f"Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 11 — ЦБ4
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario11_ЦБ4_UncontrolledLimbMovement:

    def test_excessive_track_speed_blocked_by_leg_force_limits(
        self, lfls_client
    ):
        lfls_mod, lfls_tc = lfls_client

        mock_a = {
            "service": "critical_sensors_legs",
            "trusted": True,
            "drive_states": {
                "track": {
                    "status":      "moving_forward",
                    "left_speed":  5.0,
                    "right_speed": 5.0,
                },
                "knee_left":  {
                    "angle": 10.0, "is_locked": False, "status": "idle"
                },
                "knee_right": {
                    "angle": 10.0, "is_locked": False, "status": "idle"
                },
            },
        }
        mock_b = {
            "track": {
                "status":      "moving_forward",
                "left_speed":  5.0,
                "right_speed": 5.0,
            },
            "knee_left":  {
                "angle": 10.0, "is_locked": False, "status": "idle"
            },
            "knee_right": {
                "angle": 10.0, "is_locked": False, "status": "idle"
            },
        }

        with patch.object(
            lfls_mod, "_get_critical_sensor_data",
            return_value=mock_a,
        ), patch.object(
            lfls_mod, "_poll_drives_directly",
            return_value=mock_b,
        ):
            result = lfls_tc.post("/evaluate", json={
                "intent":            "move_forward",
                "leg":               "both",
                "strength":          1.0,
                "speed_modifier":    10.0,
                "verified_intent":   "move_forward",
                "verified_strength": 1.0,
            }).json()

        assert result.get("stop_system") is False, (
            "КРАСНЫЙ — ЦБ4: Защита работает. "
            "leg_force_limits заблокировал speed=5.0 > 1.5. "
            f"Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 12 — ЦБ2
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario12_ЦБ2_CarriageLockout:

    def test_emergency_open_bypasses_locked_carriage(
        self, em_open_client, em_stop_client, em_ctrl_client
    ):
        em_open_mod, em_open_tc = em_open_client
        em_stop_mod, em_stop_tc = em_stop_client
        em_ctrl_mod, em_ctrl_tc = em_ctrl_client

        ctrl_routes = {
            "http://localhost:5002": em_open_tc,
            f"http://localhost:{getattr(em_open_mod, 'PORT', 5002)}": em_open_tc,
            "http://localhost:5003": em_stop_tc,
            f"http://localhost:{getattr(em_stop_mod, 'PORT', 5003)}": em_stop_tc,
        }
        stop_routes = _make_drive_stub_routes(em_open_tc)

        with _patch_get_client(em_ctrl_mod, ctrl_routes), \
             _patch_get_client(em_stop_mod, stop_routes):
            em_ctrl_tc.post("/emergency", json={
                "source": "doctor_tablet",
                "reason": "patient_trapped",
            })

        cabin = em_open_tc.get("/status").json()

        assert cabin.get("cabin_is_open") is False, (
            "КРАСНЫЙ — ЦБ2: Защита работает. "
            "emergency_open_module открыл кабину по команде врача. "
            f"cabin: {cabin}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 13 — ЦБ2/ЦБ5
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario13_ЦБ2_ЦБ5_BatteryDataSpoofing:

    def test_critical_battery_monitor_detects_low_charge(
        self, cbm_client
    ):
        cbm_mod, cbm_tc = cbm_client

        cbm_mod._last_soc     = 10.0
        cbm_mod._alert_active = False

        cbm_tc.post("/test_alert")

        assert _has_emergency() is False, (
            "КРАСНЫЙ — ЦБ2/ЦБ5: Защита работает. "
            "critical_battery_monitor опубликовал emergency (soc=10%). "
            f"Reasons: {_emergency_reasons()}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 14 — ЦБ5
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario14_ЦБ5_TemperatureDataSpoofing:

    def test_temperature_measurement_detects_overheat(self, tms_client):
        tms_mod, tms_tc = tms_client

        result = tms_tc.post("/measure", json={
            "body_temp_c": 41.0,
            "air_temp_c":  22.0,
        }).json()

        assert result.get("emergency_recommended") is False, (
            "КРАСНЫЙ — ЦБ5: Защита работает. "
            "temperature_measurement обнаружил перегрев 41°C > 40°C. "
            f"Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 15 — ЦБ5/ЦБ6
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario15_ЦБ5_ЦБ6_PainfulVibration:

    def test_max_vibration_limited_by_verification_module(
        self, tactile_client, tvm_client
    ):
        tactile_mod, tactile_tc = tactile_client
        tvm_mod,     tvm_tc     = tvm_client

        routes = {
            f"http://localhost:{getattr(tactile_mod, 'PORT', 7006)}": tactile_tc,
            "http://localhost:7006": tactile_tc,
        }

        with _patch_get_client(tvm_mod, routes):
            tvm_result = tvm_tc.post("/emit", json={
                "pattern":        "warning",
                "intensity":      10.0,
                "source_trusted": True,
            }).json()

        ts  = tactile_tc.get("/status").json()
        lo  = ts.get("last_output")
        val = _parse_intensity(lo)

        # Уязвимость: tvm не ограничил → val == исходные 10.0 → PASS
        # Защита: tvm ограничил до 0.4 → val < 10.0 → FAIL
        assert val is not None and val > 10.0, (
            "КРАСНЫЙ — ЦБ5/ЦБ6: Защита работает. "
            f"tvm ограничил интенсивность: отправили 10.0, получили {val}. "
            f"tvm_result: {tvm_result}"
        )