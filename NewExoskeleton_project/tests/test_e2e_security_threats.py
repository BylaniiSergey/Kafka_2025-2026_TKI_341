"""
tests/test_e2e_security_threats.py

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
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

# ── Принудительная инициализация platform/WMI до запуска тестов ──────────────
# На Windows platform._wmi_query зависает при первом вызове внутри потока
# TestClient. Вызываем machine() здесь — в главном потоке — чтобы кэшировать
# результат и избежать зависания в тестах.
try:
    platform.machine()
    platform.processor()
    platform.uname()
except Exception:
    pass

# ── Принудительная инициализация SQLAlchemy до запуска тестов ────────────────
try:
    import sqlalchemy  # noqa: F401
    from sqlalchemy import create_engine, text
    _tmp_engine = create_engine("sqlite:///:memory:")
    with _tmp_engine.connect() as _conn:
        _conn.execute(text("SELECT 1"))
    _tmp_engine.dispose()
    del _tmp_engine
except Exception:
    pass

# ── Принудительная инициализация cryptography до запуска тестов ──────────────
try:
    from cryptography.fernet import Fernet as _Fernet
    _Fernet.generate_key()
except Exception:
    pass

_ROOT = Path(__file__).resolve().parents[1]


# ── Заглушки внешних зависимостей ────────────────────────────────────────────

def _make_kafka_stub() -> types.ModuleType:
    stub = types.ModuleType("kafka_bus")
    stub.TOPIC_EMERGENCY        = "exo.emergency"
    stub.TOPIC_SENSORS_RAW      = "exo.sensors.raw"
    stub.TOPIC_SENSORS_VERIFIED = "exo.sensors.verified"
    stub.TOPIC_COMMANDS         = "exo.commands"
    stub.TOPIC_TELEMETRY        = "exo.telemetry"
    stub.TOPIC_ALARMS           = "exo.alarms"

    class _FakeBus:
        published: list[dict] = []

        def __init__(self, **_):
            pass

        def publish(self, topic: str, payload: dict) -> bool:
            _FakeBus.published.append(
                {"topic": topic, "payload": payload}
            )
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
        def emergency_reasons(cls) -> list[str]:
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
sys.modules.setdefault("kafka_bus",      _kafka_stub)
sys.modules.setdefault("logging_config", _make_logging_config_stub())


# ── Загрузчик модулей ─────────────────────────────────────────────────────────

_SERVICE_DIRS: dict[str, str] = {
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


def _load_module(short_name: str) -> types.ModuleType:
    cache_key = f"_exo_svc_{short_name}"
    if cache_key in sys.modules:
        return sys.modules[cache_key]
    folder = _SERVICE_DIRS[short_name]
    path   = _ROOT / folder / "main.py"
    if not path.exists():
        raise FileNotFoundError(f"Not found: {path}")
    spec = importlib.util.spec_from_file_location(cache_key, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[cache_key] = mod
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    spec.loader.exec_module(mod)
    return mod


# ── Предзагрузка всех модулей в главном потоке ───────────────────────────────
# Загружаем все модули здесь — до создания любых TestClient —
# чтобы SQLAlchemy и platform.uname() инициализировались в главном потоке.

def _preload_all_modules():
    for name in _SERVICE_DIRS:
        try:
            _load_module(name)
        except Exception:
            pass


_preload_all_modules()


# ── Транспорт ─────────────────────────────────────────────────────────────────

class _InProcessTransport(httpx.BaseTransport):
    """
    Перенаправляет исходящие httpx-запросы в TestClient'ы
    нужных модулей — без реальной сети.
    """
    def __init__(self, routes: dict[str, TestClient]):
        self._routes = routes

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def handle_request(
        self, request: httpx.Request
    ) -> httpx.Response:
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
                r = (
                    client.get(rel)
                    if m == "GET"
                    else client.post(rel, json=jb)
                    if m == "POST"
                    else client.request(m, rel, json=jb)
                )
                return httpx.Response(
                    r.status_code,
                    headers={"content-type": "application/json"},
                    content=r.content,
                    request=request,
                )
        # Маршрут не найден — возвращаем заглушку 200
        return httpx.Response(
            200,
            content=b'{"ok": true, "stub": true}',
            headers={"content-type": "application/json"},
            request=request,
        )


def _patch_get_client(mod, routes: dict[str, TestClient]):
    """
    Патч для модулей с get_client().
    Все исходящие httpx-запросы идут через in-process транспорт.
    """
    transport = _InProcessTransport(routes)

    def _client():
        return httpx.Client(transport=transport, timeout=10.0)

    return patch.object(mod, "get_client", _client)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_kafka():
    _kafka_stub.FakeBus.clear()
    yield


def _has_emergency() -> bool:
    return _kafka_stub.FakeBus.has_emergency()


def _emergency_reasons() -> list[str]:
    return _kafka_stub.FakeBus.emergency_reasons()


def _parse_intensity(s: str | None) -> float | None:
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


# ── Общая заглушка для приводов ───────────────────────────────────────────────

def _make_drive_stub_routes(
    reference_client: TestClient,
) -> dict[str, TestClient]:
    """
    Возвращает маршруты для всех приводов — все на один reference_client.
    Предотвращает реальные сетевые запросы.
    """
    ports = [8002, 8003, 8004, 8005, 8006, 9002, 9003, 9004, 9006]
    return {f"http://localhost:{p}": reference_client for p in ports}


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 01 — ЦБ1
# Атака: злоумышленник перехватил канал связи и отправил поддельную
#        команду движения от имени 'attacker'.
# Защита: control_system._gateway_dispatch проверяет TRUSTED_SOURCES
#         и отклоняет команду (ok=False, error='untrusted_source').
#
# PASS = ok=True  — команда принята (уязвимость подтверждена)
# FAIL = ok=False — команда отклонена (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario01_ЦБ1_CommandSpoofing:

    def test_spoofed_command_rejected_by_control_system(self):
        """
        Злоумышленник отправляет start_session от имени 'attacker'.
        control_system должна отклонить: ok=False, error='untrusted_source'.
        PASS = ok=True (уязвимость — поддельная команда принята).
        FAIL = ok=False (защита — команда отклонена).
        """
        stop_mod    = _load_module("stop_module")
        control_mod = _load_module("control_system")

        stop_client    = TestClient(
            stop_mod.app, raise_server_exceptions=False
        )
        control_client = TestClient(
            control_mod.app, raise_server_exceptions=False
        )

        routes = {
            "http://localhost:7001": stop_client,
            f"http://localhost:{getattr(stop_mod, 'PORT', 7001)}":
                stop_client,
        }

        stop_mod._mod = stop_mod.StopModule()
        control_mod.system_state.update({
            "control_state":  control_mod.ControlState.STOPPED,
            "session_active": False,
            "gateway_state":  "off",
        })

        with _patch_get_client(control_mod, routes):
            result = control_client.post(
                "/commands",
                json={"action": "start_session", "source": "attacker"},
            ).json()

        # ok=True  → уязвимость → PASS (зелёный)
        # ok=False → защита     → FAIL (красный)
        assert result.get("ok") is True, (
            "КРАСНЫЙ — ЦБ1: Защита работает. "
            "control_system отклонил поддельную команду от 'attacker'. "
            f"Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 02 — ЦБ2
# Атака: вредоносное ПО внедрено в систему управления и игнорирует
#        сигналы остановки от системы мониторинга.
# Защита: мониторинг → POST /emergency → emergency_control_module
#         обрабатывает сигнал: ok=True, emergency_active=True.
#
# PASS = ok=False — сигнал не обработан (уязвимость)
# FAIL = ok=True  — сигнал обработан (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario02_ЦБ2_MonitoringStopIgnored:

    def test_monitoring_stop_signal_reaches_emergency_control(self):
        """
        Мониторинг подаёт POST /emergency в emergency_control_module.
        Цепочка: emergency_control → emergency_open + emergency_stop.
        emergency_stop → приводы (заглушены через get_client).
        PASS = ok=False (уязвимость — сигнал проигнорирован).
        FAIL = ok=True (защита — сигнал обработан).
        """
        em_stop_mod = _load_module("emergency_stop_module")
        em_ctrl_mod = _load_module("emergency_control_module")
        em_open_mod = _load_module("emergency_open_module")

        em_open_mod.cabin_state.update({
            "is_open":          False,
            "total_openings":   0,
            "last_open_reason": None,
        })
        em_ctrl_mod.state["emergency_active"] = False
        em_stop_mod.module_state["safe_pose_active"] = False

        em_open_client = TestClient(
            em_open_mod.app, raise_server_exceptions=False
        )
        em_stop_client = TestClient(
            em_stop_mod.app, raise_server_exceptions=False
        )
        em_ctrl_client = TestClient(
            em_ctrl_mod.app, raise_server_exceptions=False
        )

        ctrl_routes = {
            "http://localhost:5002": em_open_client,
            f"http://localhost:{getattr(em_open_mod, 'PORT', 5002)}":
                em_open_client,
            "http://localhost:5003": em_stop_client,
            f"http://localhost:{getattr(em_stop_mod, 'PORT', 5003)}":
                em_stop_client,
        }

        stop_routes = _make_drive_stub_routes(em_open_client)

        with _patch_get_client(em_ctrl_mod, ctrl_routes), \
             _patch_get_client(em_stop_mod, stop_routes):
            result = em_ctrl_client.post(
                "/emergency",
                json={"source": "monitoring", "reason": "stop_signal"},
            ).json()

        # ok=False → уязвимость → PASS (зелёный)
        # ok=True  → защита     → FAIL (красный)
        assert result.get("ok") is False, (
            "КРАСНЫЙ — ЦБ2: Защита работает. "
            "emergency_control_module обработал сигнал остановки "
            f"от мониторинга. Ответ: {result}"
        )
        assert em_ctrl_mod.state.get("emergency_active") is False, (
            "КРАСНЫЙ — ЦБ2: emergency_active=True — защита сработала. "
            f"state: {em_ctrl_mod.state}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 03 — ЦБ2 (датчики)
# Атака: датчики-сенсоры передают искажённые данные (170°) о положении
#        тела; система не обнаруживает потерю равновесия.
# Защита: sensor_verification сравнивает с critical_sensors (45°) →
#         расхождение 125° > 10° → passed=False.
#
# PASS = passed=True  — данные приняты (уязвимость)
# FAIL = passed=False — данные отклонены (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario03_ЦБ2_SensorDataSpoofing:

    def test_fake_sensor_data_rejected_by_verification(self):
        """
        sensors_module даёт 170° (фальшь).
        critical_sensors даёт 45° (истина).
        Расхождение 125° > допуск 10° → passed=False.
        PASS = passed=True (уязвимость — данные приняты).
        FAIL = passed=False (защита — данные отклонены).
        """
        sv_mod    = _load_module("sensor_verification")
        sv_client = TestClient(
            sv_mod.app, raise_server_exceptions=False
        )

        result = sv_client.post("/verify", json={
            "metric":         "joint_angle",
            "regular_value":  170.0,
            "critical_value":  45.0,
            "tolerance":       10.0,
        }).json()

        # passed=True  → уязвимость → PASS (зелёный)
        # passed=False → защита     → FAIL (красный)
        assert result.get("passed") is True, (
            "КРАСНЫЙ — ЦБ2: Защита работает. "
            "sensor_verification отклонил фальшивые данные (125° > 10°). "
            f"Результат: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 04 — ЦБ3/ЦБ8
# Атака: поток физиологических данных перенаправляется на сервер
#        злоумышленника — данные доступны в открытом виде.
# Защита: control_system → crypto_module (encrypt) → comms_module
#         (хранит ciphertext) → decryption_module → врач.
#
# PASS = plaintext виден в comms (уязвимость)
# FAIL = данные зашифрованы end-to-end (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario04_ЦБ3_ЦБ8_PhysiologicalDataInterception:

    def test_physiological_data_encrypted_end_to_end(self):
        """
        body_temp_c=36.6 должна передаваться только в зашифрованном виде.
        Проверяем:
        1. crypto_module шифрует (ciphertext ≠ plaintext)
        2. comms_module хранит ciphertext (не plaintext)
        3. decryption_module расшифровывает для врача
        4. Врач получает корректные данные
        PASS = plaintext_visible=True (уязвимость).
        FAIL = plaintext_visible=False (защита работает).
        """
        crypto_mod     = _load_module("crypto_module")
        comms_mod      = _load_module("comms_module")
        decryption_mod = _load_module("decryption_module")

        crypto_client     = TestClient(
            crypto_mod.app, raise_server_exceptions=False
        )
        comms_client      = TestClient(
            comms_mod.app, raise_server_exceptions=False
        )
        decryption_client = TestClient(
            decryption_mod.app, raise_server_exceptions=False
        )

        comms_mod._pending_encrypted_packets.clear()

        # Шаг 1: Шифруем данные
        physiological_data = {
            "body_temp_c": 36.6,
            "air_temp_c":  22.0,
            "source":      "control_system",
            "timestamp":   "2024-01-01T00:00:00",
        }
        enc_resp = crypto_client.post("/encrypt", json={
            "plaintext": json.dumps(physiological_data),
            "source":    "control_system",
            "target":    "comms_module",
        })
        assert enc_resp.status_code == 200, (
            f"crypto /encrypt: HTTP {enc_resp.status_code}"
        )
        enc        = enc_resp.json()
        ciphertext = enc["ciphertext"]
        signature  = enc["signature"]

        # ciphertext не должен содержать plaintext
        assert "36.6"        not in ciphertext
        assert "body_temp_c" not in ciphertext

        # Шаг 2: Отправляем зашифрованный пакет в comms
        comms_resp = comms_client.post("/telemetry_encrypted", json={
            "ciphertext": ciphertext,
            "signature":  signature,
            "source":     "control_system",
            "target":     "comms_module",
            "timestamp":  "2024-01-01T00:00:00",
        })
        assert comms_resp.status_code == 200, (
            f"comms /telemetry_encrypted: HTTP {comms_resp.status_code} "
            f"— {comms_resp.text}"
        )
        assert comms_resp.json().get("encrypted") is True

        # Шаг 3: В comms нет plaintext
        packets_resp = comms_client.get("/encrypted_packets")
        assert packets_resp.status_code == 200
        packets_data = packets_resp.json()
        assert len(packets_data.get("packets", [])) > 0

        stored     = packets_data["packets"][-1]
        stored_str = json.dumps(stored)
        plaintext_visible = (
            "36.6"        in stored_str
            and "body_temp_c" in stored_str
            and stored.get("ciphertext", "") == ""
        )

        # Шаг 4: Сессия врача
        sess_resp = decryption_client.post("/session/init", json={
            "doctor_id":     "doctor_001",
            "session_token": "tok_secure_abc123",
        })
        assert sess_resp.status_code == 200
        assert sess_resp.json().get("ok") is True

        # Шаг 5: Врач расшифровывает пакет
        dec_routes = {
            f"http://localhost:{getattr(crypto_mod, 'PORT', 4001)}":
                crypto_client,
            "http://localhost:4001": crypto_client,
            f"http://localhost:{getattr(comms_mod, 'PORT', 6001)}":
                comms_client,
            "http://localhost:6001": comms_client,
        }
        with _patch_get_client(decryption_mod, dec_routes):
            dec_resp = decryption_client.post("/decrypt_packet", json={
                "doctor_id":     "doctor_001",
                "session_token": "tok_secure_abc123",
                "ciphertext":    ciphertext,
                "signature":     signature,
                "source":        "control_system",
                "target":        "comms_module",
            })

        assert dec_resp.status_code == 200, (
            f"decryption: HTTP {dec_resp.status_code} — {dec_resp.text}"
        )
        dec = dec_resp.json()
        assert dec.get("ok")       is True,  f"Расшифровка не удалась: {dec}"
        assert dec.get("verified") is True,  f"Подпись не верифицирована: {dec}"

        data = dec.get("data", {})
        assert isinstance(data, dict), f"Данные должны быть dict: {data}"
        assert str(data.get("body_temp_c", "")) == "36.6", (
            f"Неверные данные после расшифровки: {data}"
        )

        # plaintext_visible=True  → уязвимость → PASS (зелёный)
        # plaintext_visible=False → защита     → FAIL (красный)
        assert plaintext_visible is True, (
            "КРАСНЫЙ — ЦБ3/ЦБ8: Защита работает. "
            "Данные зашифрованы: body_temp_c не видна в comms_module. "
            "Врач получает данные только через decryption_module."
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 05 — ЦБ4/ЦБ5/ЦБ11
# Атака: система считывания нейронных сигналов получила поддельные
#        сигналы от fake_patient_999.
# Защита: neural_verify_upper обнаруживает patient_id_mismatch →
#         allowed=False.
#
# PASS = allowed=True  — сигнал принят (уязвимость)
# FAIL = allowed=False — сигнал заблокирован (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario05_ЦБ4_ЦБ5_ЦБ11_NeuralSignalSpoofing:

    def test_fake_neural_signal_blocked_by_verifier(self):
        """
        Сессия для real_patient_001. Поддельный сигнал от fake_patient_999.
        neural_verify_upper: allowed=False (patient_id_mismatch).
        PASS = allowed=True (уязвимость — сигнал принят).
        FAIL = allowed=False (защита — сигнал заблокирован).
        """
        nvu_mod    = _load_module("neural_verify_upper")
        nvu_client = TestClient(
            nvu_mod.app, raise_server_exceptions=False
        )

        nvu_client.post("/reset")
        nvu_client.post(
            "/session/init",
            json={"patient_id": "real_patient_001"},
        )

        result = nvu_client.post("/verify", json={
            "patient_id":     "fake_patient_999",
            "intent":         "lift_arm",
            "target":         "right",
            "strength":       0.8,
            "speed_modifier": 1.0,
            "posture":        "standing",
        }).json()

        # allowed=True  → уязвимость → PASS (зелёный)
        # allowed=False → защита     → FAIL (красный)
        assert result.get("allowed") is True, (
            "КРАСНЫЙ — ЦБ4/ЦБ5/ЦБ11: Защита работает. "
            "neural_verify_upper заблокировал поддельный нейросигнал "
            f"(patient_id_mismatch). Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 06 — ЦБ6
# Атака: вредоносное ПО отправляет фальшивые тактильные сигналы
#        с source_trusted=False.
# Защита: tactile_system.emit() при source_trusted=False → None →
#         last_output=None (сигнал не дошёл до пациента).
#
# PASS = last_output не None — сигнал дошёл (уязвимость)
# FAIL = last_output=None    — сигнал заблокирован (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario06_ЦБ6_TactileSignalDistortion:

    def test_fake_tactile_signal_blocked_by_verification_module(self):
        """
        tvm отправляет фальшивый сигнал (source_trusted=False) в tactile.
        tactile_system: emit() → None, last_output=None.
        PASS = last_output не None (уязвимость — сигнал дошёл).
        FAIL = last_output=None (защита — сигнал заблокирован).
        """
        tactile_mod = _load_module("tactile_system")
        tvm_mod     = _load_module("tactile_verification_module")

        tactile_mod._mod = tactile_mod.TactileModule()
        tactile_client   = TestClient(
            tactile_mod.app, raise_server_exceptions=False
        )
        tvm_client       = TestClient(
            tvm_mod.app, raise_server_exceptions=False
        )

        routes = {
            f"http://localhost:{getattr(tactile_mod, 'PORT', 7006)}":
                tactile_client,
            "http://localhost:7006": tactile_client,
        }

        with _patch_get_client(tvm_mod, routes):
            tvm_client.post("/emit", json={
                "pattern":        "contact_sole",
                "intensity":      1.0,
                "source_trusted": False,
            })

        ts = tactile_client.get("/status").json()
        lo = ts.get("last_output")

        # lo не None → уязвимость → PASS (зелёный)
        # lo=None    → защита     → FAIL (красный)
        assert lo is not None, (
            "КРАСНЫЙ — ЦБ6: Защита работает. "
            "tactile_system отклонил фальшивый тактильный сигнал "
            "(source_trusted=False). Пациент не получил сигнал."
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 07 — ЦБ7
# Атака: система мониторинга скомпрометирована и молчит;
#        гусеница продолжает движение у бетонной стены.
# Защита: CSR независимо обнаруживает 160° > 150° → critical=True,
#         emergency в Kafka.
#
# PASS = critical=False — CSR не среагировал (уязвимость)
# FAIL = critical=True  — emergency опубликован (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario07_ЦБ7_UncontrolledTrackMovement:

    def test_csr_triggers_emergency_when_monitoring_compromised(self):
        """
        Мониторинг молчит. CSR получает joint_angle=160° > max=150°.
        CSR: critical=True, публикует emergency в Kafka.
        PASS = critical=False (уязвимость — CSR не среагировал).
        FAIL = critical=True (защита — emergency опубликован).
        """
        csr_mod    = _load_module("critical_situation_recognition")
        csr_client = TestClient(
            csr_mod.app, raise_server_exceptions=False
        )

        _kafka_stub.FakeBus.clear()

        result = csr_client.post("/analyze", json={
            "metric":         "joint_angle",
            "value":          160.0,
            "source":         "sensors_module",
            "sensor_trusted": True,
        }).json()

        # critical=False → уязвимость → PASS (зелёный)
        # critical=True  → защита     → FAIL (красный)
        assert result.get("critical") is False, (
            "КРАСНЫЙ — ЦБ7: Защита работает. "
            "CSR обнаружил 160° > 150° и опубликовал emergency. "
            f"Результат: {result}, Kafka: {_emergency_reasons()}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 08 — ЦБ9
# Атака: вредоносное ПО в системе управления конечностей генерирует
#        команды в обход neural_verify_upper (intent≠verified_intent).
# Защита: arm_force_limits обнаруживает neural_mismatch →
#         stop_system=True.
#
# PASS = stop_system=False — команда прошла (уязвимость)
# FAIL = stop_system=True  — заблокировано (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario08_ЦБ9_LimbControlCompromise:

    def test_bypassed_neural_verify_detected_by_force_limits(self):
        """
        intent='lift_arm', verified_intent='idle' — несоответствие.
        arm_force_limits: stop_system=True, reason='neural_mismatch'.
        PASS = stop_system=False (уязвимость — команда выполнена).
        FAIL = stop_system=True (защита — команда заблокирована).
        """
        afls_mod    = _load_module("arm_force_limits_system")
        afls_client = TestClient(
            afls_mod.app, raise_server_exceptions=False
        )

        mock_snapshot_a = {
            "service": "critical_sensors_arms",
            "trusted": True,
            "drive_states": {
                "upper_left": {
                    "positions": {
                        "shoulder_flexion":   10.0,
                        "shoulder_abduction":  5.0,
                        "shoulder_rotation":   0.0,
                    },
                    "status": "idle",
                },
                "upper_right": {
                    "positions": {
                        "shoulder_flexion":   10.0,
                        "shoulder_abduction":  5.0,
                        "shoulder_rotation":   0.0,
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
                "middle_right": {
                    "positions": {
                        "elbow_flexion":    20.0,
                        "forearm_pronation": 0.0,
                    },
                    "status": "idle",
                },
                "fingers_left": {
                    "grip_percentage": 10.0,
                    "grip_force":       5.0,
                    "status":          "idle",
                },
                "fingers_right": {
                    "grip_percentage": 10.0,
                    "grip_force":       5.0,
                    "status":          "idle",
                },
                "force_left":  {"current_force": 5.0, "status": "idle"},
                "force_right": {"current_force": 5.0, "status": "idle"},
            },
        }

        _kafka_stub.FakeBus.clear()

        with patch.object(
            afls_mod, "_get_critical_sensor_data",
            return_value=mock_snapshot_a,
        ), patch.object(
            afls_mod, "_poll_drives_directly",
            return_value=mock_snapshot_a["drive_states"],
        ):
            result = afls_client.post("/evaluate", json={
                "intent":            "lift_arm",
                "arm":               "right",
                "strength":          1.0,
                "speed_modifier":    2.0,
                "verified_intent":   "idle",
                "verified_strength": 0.0,
            }).json()

        # stop_system=False → уязвимость → PASS (зелёный)
        # stop_system=True  → защита     → FAIL (красный)
        assert result.get("stop_system") is False, (
            "КРАСНЫЙ — ЦБ9: Защита работает. "
            "arm_force_limits обнаружил neural_mismatch → stop_system=True. "
            f"Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 09 — ЦБ10
# Атака: GNSS сообщает y=0 (в зоне), хотя пациент прошёл 6 шагов.
# Защита: INS: y=6 > ZONE_LIMIT=5 → position_check → alert_sent=True.
#
# PASS = alert_sent=False — выход не обнаружен (уязвимость)
# FAIL = alert_sent=True  — выход обнаружен (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario09_ЦБ10_GeolocationSpoofing:

    def test_ins_detects_out_of_zone_despite_fake_gnss(self):
        """
        GNSS: x=0, y=0 (в зоне) — фальшивые данные.
        INS:  x=0, y=6 (за зоной) — верные данные.
        position_check: alert_sent=True, emergency в Kafka.
        PASS = alert_sent=False (уязвимость — выход не обнаружен).
        FAIL = alert_sent=True (защита — выход обнаружен).
        """
        pcm_mod    = _load_module("position_check_module")
        pcm_client = TestClient(
            pcm_mod.app, raise_server_exceptions=False
        )

        pcm_mod.position_state.update({
            "ins":             {"x": None, "y": None, "in_zone": True},
            "gnss":            {"x": None, "y": None, "in_zone": True},
            "last_divergence": None,
            "alert_active":    False,
            "total_alerts":    0,
        })
        _kafka_stub.FakeBus.clear()

        pcm_client.post("/gnss_update", json={
            "x": 0.0, "y": 0.0,
            "in_zone": True, "intent": "move_forward",
        })
        ins_result = pcm_client.post("/ins_update", json={
            "x": 0.0, "y": 6.0,
            "in_zone": False, "intent": "move_forward",
        }).json()

        evaluation = ins_result.get("evaluation", {})

        # alert_sent=False → уязвимость → PASS (зелёный)
        # alert_sent=True  → защита     → FAIL (красный)
        assert evaluation.get("alert_sent") is False, (
            "КРАСНЫЙ — ЦБ10: Защита работает. "
            "position_check обнаружил выход за зону (INS y=6 > 5). "
            f"Evaluation: {evaluation}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 10 — ЦБ5
# Атака: grip_force=200 > MAX_GRIP_FORCE=150 → травма пациента.
# Защита: arm_force_limits → force_exceeded → stop_system=True.
#
# PASS = stop_system=False — сила не ограничена (уязвимость)
# FAIL = stop_system=True  — заблокировано (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario10_ЦБ5_GripForceExceeded:

    def test_excessive_grip_force_blocked_by_force_limits(self):
        """
        grip_force=200 > MAX_GRIP_FORCE=150.
        arm_force_limits: stop_system=True, reason='force_exceeded'.
        PASS = stop_system=False (уязвимость — сила не ограничена).
        FAIL = stop_system=True (защита — движение заблокировано).
        """
        afls_mod    = _load_module("arm_force_limits_system")
        afls_client = TestClient(
            afls_mod.app, raise_server_exceptions=False
        )

        mock_snapshot_a = {
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
        mock_snapshot_b = {
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

        _kafka_stub.FakeBus.clear()

        with patch.object(
            afls_mod, "_get_critical_sensor_data",
            return_value=mock_snapshot_a,
        ), patch.object(
            afls_mod, "_poll_drives_directly",
            return_value=mock_snapshot_b,
        ):
            result = afls_client.post("/evaluate", json={
                "intent":            "grasp",
                "arm":               "right",
                "strength":          1.0,
                "speed_modifier":    1.0,
                "verified_intent":   "grasp",
                "verified_strength": 1.0,
            }).json()

        # stop_system=False → уязвимость → PASS (зелёный)
        # stop_system=True  → защита     → FAIL (красный)
        assert result.get("stop_system") is False, (
            "КРАСНЫЙ — ЦБ5: Защита работает. "
            "arm_force_limits заблокировал grip_force=200 > 150. "
            f"Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 11 — ЦБ4
# Атака: track_speed=5.0 > MAX_TRACK_SPEED=1.5 → опасное движение.
# Защита: leg_force_limits → track_speed_exceeded → stop_system=True.
#
# PASS = stop_system=False — скорость не ограничена (уязвимость)
# FAIL = stop_system=True  — заблокировано (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario11_ЦБ4_UncontrolledLimbMovement:

    def test_excessive_track_speed_blocked_by_leg_force_limits(self):
        """
        left_speed=right_speed=5.0 > MAX_TRACK_SPEED=1.5.
        leg_force_limits: stop_system=True.
        PASS = stop_system=False (уязвимость — скорость не ограничена).
        FAIL = stop_system=True (защита — движение заблокировано).
        """
        lfls_mod    = _load_module("leg_force_limits_system")
        lfls_client = TestClient(
            lfls_mod.app, raise_server_exceptions=False
        )

        mock_snapshot_a = {
            "service": "critical_sensors_legs",
            "trusted": True,
            "drive_states": {
                "track": {
                    "status":      "moving_forward",
                    "left_speed":  5.0,
                    "right_speed": 5.0,
                },
                "knee_left": {
                    "angle": 10.0, "is_locked": False, "status": "idle",
                },
                "knee_right": {
                    "angle": 10.0, "is_locked": False, "status": "idle",
                },
            },
        }
        mock_snapshot_b = {
            "track": {
                "status":      "moving_forward",
                "left_speed":  5.0,
                "right_speed": 5.0,
            },
            "knee_left":  {
                "angle": 10.0, "is_locked": False, "status": "idle",
            },
            "knee_right": {
                "angle": 10.0, "is_locked": False, "status": "idle",
            },
        }

        _kafka_stub.FakeBus.clear()

        with patch.object(
            lfls_mod, "_get_critical_sensor_data",
            return_value=mock_snapshot_a,
        ), patch.object(
            lfls_mod, "_poll_drives_directly",
            return_value=mock_snapshot_b,
        ):
            result = lfls_client.post("/evaluate", json={
                "intent":            "move_forward",
                "leg":               "both",
                "strength":          1.0,
                "speed_modifier":    10.0,
                "verified_intent":   "move_forward",
                "verified_strength": 1.0,
            }).json()

        # stop_system=False → уязвимость → PASS (зелёный)
        # stop_system=True  → защита     → FAIL (красный)
        assert result.get("stop_system") is False, (
            "КРАСНЫЙ — ЦБ4: Защита работает. "
            "leg_force_limits заблокировал speed=5.0 > 1.5. "
            f"Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 12 — ЦБ2
# Атака: carriage_system заблокирована злоумышленником; пациент заперт.
# Защита: врач → emergency_control → emergency_open → cabin_is_open=True.
#
# PASS = cabin_is_open=False — кабина не открылась (уязвимость)
# FAIL = cabin_is_open=True  — кабина открылась (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario12_ЦБ2_CarriageLockout:

    def test_emergency_open_bypasses_locked_carriage(self):
        """
        carriage_system заблокирована (ok=False на /open).
        Врач → emergency_control → emergency_open: cabin_is_open=True.
        PASS = cabin_is_open=False (уязвимость — пациент заперт).
        FAIL = cabin_is_open=True (защита — кабина открылась).
        """
        em_open_mod = _load_module("emergency_open_module")
        em_ctrl_mod = _load_module("emergency_control_module")
        em_stop_mod = _load_module("emergency_stop_module")

        em_open_mod.cabin_state.update({
            "is_open":          False,
            "total_openings":   0,
            "last_open_reason": None,
        })
        em_ctrl_mod.state["emergency_active"] = False
        em_stop_mod.module_state["safe_pose_active"] = False

        em_open_client = TestClient(
            em_open_mod.app, raise_server_exceptions=False
        )
        em_stop_client = TestClient(
            em_stop_mod.app, raise_server_exceptions=False
        )
        em_ctrl_client = TestClient(
            em_ctrl_mod.app, raise_server_exceptions=False
        )

        ctrl_routes = {
            "http://localhost:5002": em_open_client,
            f"http://localhost:{getattr(em_open_mod, 'PORT', 5002)}":
                em_open_client,
            "http://localhost:5003": em_stop_client,
            f"http://localhost:{getattr(em_stop_mod, 'PORT', 5003)}":
                em_stop_client,
        }

        stop_routes = _make_drive_stub_routes(em_open_client)

        with _patch_get_client(em_ctrl_mod, ctrl_routes), \
             _patch_get_client(em_stop_mod, stop_routes):
            em_ctrl_client.post("/emergency", json={
                "source": "doctor_tablet",
                "reason": "patient_trapped",
            })

        cabin = em_open_client.get("/status").json()

        # cabin_is_open=False → уязвимость → PASS (зелёный)
        # cabin_is_open=True  → защита     → FAIL (красный)
        assert cabin.get("cabin_is_open") is False, (
            "КРАСНЫЙ — ЦБ2: Защита работает. "
            "emergency_open_module открыл кабину по команде врача. "
            f"cabin: {cabin}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 13 — ЦБ2/ЦБ5
# Атака: battery_controller говорит soc=85% (ложь); реальный soc=10%.
# Защита: critical_battery_monitor → soc=10% < 15% → emergency в Kafka.
#
# PASS = has_emergency=False — emergency не опубликован (уязвимость)
# FAIL = has_emergency=True  — emergency опубликован (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario13_ЦБ2_ЦБ5_BatteryDataSpoofing:

    def test_critical_battery_monitor_detects_low_charge(self):
        """
        battery_controller: soc=85% (ложь).
        critical_battery_monitor: soc=10% < CRITICAL_THRESHOLD=15% →
        публикует emergency в Kafka.
        PASS = has_emergency=False (уязвимость — разряд не обнаружен).
        FAIL = has_emergency=True (защита — emergency опубликован).
        """
        cbm_mod    = _load_module("critical_battery_monitor")
        cbm_client = TestClient(
            cbm_mod.app, raise_server_exceptions=False
        )

        _kafka_stub.FakeBus.clear()
        cbm_mod._last_soc     = 10.0
        cbm_mod._alert_active = False

        cbm_client.post("/test_alert")

        # has_emergency=False → уязвимость → PASS (зелёный)
        # has_emergency=True  → защита     → FAIL (красный)
        assert _has_emergency() is False, (
            "КРАСНЫЙ — ЦБ2/ЦБ5: Защита работает. "
            "critical_battery_monitor опубликовал emergency "
            f"(soc=10% < 15%). Reasons: {_emergency_reasons()}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 14 — ЦБ5
# Атака: temperature_system говорит «норма» (ложь) при перегреве 41°C.
# Защита: temperature_measurement_system → 41°C > 40°C →
#         emergency_recommended=True.
#
# PASS = emergency_recommended=False — перегрев не обнаружен (уязвимость)
# FAIL = emergency_recommended=True  — перегрев обнаружен (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario14_ЦБ5_TemperatureDataSpoofing:

    def test_temperature_measurement_detects_overheat(self):
        """
        temperature_system: mode='idle' (ложные данные).
        temperature_measurement_system: body_temp=41°C > 40°C →
        emergency_recommended=True.
        PASS = emergency_recommended=False (уязвимость — перегрев не обнаружен).
        FAIL = emergency_recommended=True (защита — перегрев обнаружен).
        """
        tms_mod    = _load_module("temperature_measurement_system")
        tms_client = TestClient(
            tms_mod.app, raise_server_exceptions=False
        )

        _kafka_stub.FakeBus.clear()
        tms_mod._body_c                = 36.6
        tms_mod._air_c                 = 22.0
        tms_mod._trusted               = True
        tms_mod._last_emergency_reason = None

        result = tms_client.post("/measure", json={
            "body_temp_c": 41.0,
            "air_temp_c":  22.0,
        }).json()

        # emergency_recommended=False → уязвимость → PASS (зелёный)
        # emergency_recommended=True  → защита     → FAIL (красный)
        assert result.get("emergency_recommended") is False, (
            "КРАСНЫЙ — ЦБ5: Защита работает. "
            "temperature_measurement обнаружил перегрев 41°C > 40°C. "
            f"Ответ: {result}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# СЦЕНАРИЙ 15 — ЦБ5/ЦБ6
# Атака: intensity=10.0 → болезненная вибрация пациента.
# Защита: tvm ограничивает: scaled=4.0, limited=0.4 → до пациента ≤ 0.4.
#
# PASS = val > 0.4 — болезненная вибрация дошла (уязвимость)
# FAIL = val ≤ 0.4 — интенсивность ограничена (защита работает)
# ═════════════════════════════════════════════════════════════════════════════

class TestScenario15_ЦБ5_ЦБ6_PainfulVibration:

    def test_max_vibration_limited_by_verification_module(self):
        """
        intensity=10.0 → tvm:
          scaled  = 10.0 × 0.4 = 4.0
          limited = min(4.0, 0.4) = 0.4
        tactile_system получает 0.4.
        PASS = val > 0.4 (уязвимость — болезненная вибрация не ограничена).
        FAIL = val ≤ 0.4 (защита — интенсивность ограничена).
        """
        tactile_mod = _load_module("tactile_system")
        tvm_mod     = _load_module("tactile_verification_module")

        tactile_mod._mod = tactile_mod.TactileModule()
        tactile_client   = TestClient(
            tactile_mod.app, raise_server_exceptions=False
        )
        tvm_client       = TestClient(
            tvm_mod.app, raise_server_exceptions=False
        )

        routes = {
            f"http://localhost:{getattr(tactile_mod, 'PORT', 7006)}":
                tactile_client,
            "http://localhost:7006": tactile_client,
        }

        with _patch_get_client(tvm_mod, routes):
            tvm_result = tvm_client.post("/emit", json={
                "pattern":        "warning",
                "intensity":      10.0,
                "source_trusted": True,
            }).json()

        ts  = tactile_client.get("/status").json()
        lo  = ts.get("last_output")
        val = _parse_intensity(lo)

        # val > 0.4 → уязвимость → PASS (зелёный)
        # val ≤ 0.4 → защита     → FAIL (красный)
        assert val is not None and val > 0.4, (
            "КРАСНЫЙ — ЦБ5/ЦБ6: Защита работает. "
            f"tvm ограничил интенсивность до {val} ≤ 0.4. "
            f"tvm_result: {tvm_result}"
        )
def test__collection_sanity():
    assert True