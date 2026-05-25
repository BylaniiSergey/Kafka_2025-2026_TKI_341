"""
Тесты политик безопасности монитора (services/security_monitor).

Проверяется:
  1. Все политики в tuple `policies` корректно пропускаются через
     check_operation.
  2. Запрещённые комбинации (включая «соседние» с разрешёнными)
     блокируются.
  3. Граничные случаи: пустые/неполные поля.
  4. 15 негативных сценариев из таблицы угроз (ЦБ1–ЦБ11) — для каждой
     атаки соответствующая опасная политика отсутствует, монитор её
     отклоняет.
  5. Логика consumer.handle_event: при отказе политики producer
     НЕ вызывается, при успехе — вызывается.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _install_kafka_stub() -> None:
    """
    confluent_kafka устанавливается только в Docker-контейнере
    security_monitor; на хосте при unit-тестах его может не быть.
    Подменяем модуль заглушкой, чтобы можно было импортировать
    security_monitor.consumer / .producer и тестировать чистую логику.
    """
    if "confluent_kafka" in sys.modules:
        return
    stub = types.ModuleType("confluent_kafka")
    stub.Consumer = type("Consumer", (), {})
    stub.Producer = type("Producer", (), {})
    stub.OFFSET_BEGINNING = -2
    sys.modules["confluent_kafka"] = stub


_install_kafka_stub()


def _load_security_module(name: str):
    """
    Загружаем NewExoskeleton_project/security_monitor/<name>.py как модуль
    security_monitor.<name>, чтобы относительные импорты внутри
    (например, from .policies import ...) работали.
    """
    pkg_path = _ROOT / "NewExoskeleton_project" / "security_monitor"

    if "security_monitor" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "security_monitor",
            pkg_path / "__init__.py",
            submodule_search_locations=[str(pkg_path)],
        )
        pkg = importlib.util.module_from_spec(spec)
        sys.modules["security_monitor"] = pkg
        spec.loader.exec_module(pkg)

    full = f"security_monitor.{name}"
    # Если модуль был наполовину загружен после прошлой ошибки —
    # удалить и перезагрузить с нуля.
    cached = sys.modules.get(full)
    if cached is not None and not hasattr(cached, "__file__"):
        del sys.modules[full]
    if full in sys.modules:
        return sys.modules[full]

    spec = importlib.util.spec_from_file_location(full, pkg_path / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(full, None)
        raise
    return mod


policies_mod = _load_security_module("policies")
POLICIES = policies_mod.policies
check_operation = policies_mod.check_operation


# ============================================================
# 1. Разрешённые политики
# ============================================================
class TestAllowedPolicies:
    """Каждая запись в кортеже policies должна проходить проверку."""

    @pytest.mark.parametrize("policy", POLICIES, ids=lambda p: f"{p['src']}->{p['dst']}:{p['operation']}")
    def test_each_policy_passes(self, policy):
        details = {
            "source": policy["src"],
            "deliver_to": policy["dst"],
            "operation": policy["operation"],
        }
        assert check_operation("e", details) is True

    def test_policies_count_matches_design(self):
        """Размер кортежа политик зафиксирован: 39 разрешённых обращений."""
        assert len(POLICIES) == 39

    def test_no_duplicate_policies(self):
        """В кортеже политик не должно быть дубликатов."""
        as_tuples = [(p["src"], p["dst"], p["operation"]) for p in POLICIES]
        assert len(as_tuples) == len(set(as_tuples))


# ============================================================
# 2. Запрещённые обращения
# ============================================================
class TestDeniedPolicies:
    """Все варианты, не входящие в кортеж — должны блокироваться."""

    @pytest.mark.parametrize("src,dst,op", [
        # Источники-самозванцы
        ("hacker",  "control_gateway", "emergency_stop"),
        ("attacker", "stop",           "reset_emergency"),
        ("unknown", "carriage",        "open"),

        # Правильный src, но недопустимая операция
        ("patient", "stop",            "allow_movement"),
        ("patient", "control_gateway", "reset_emergency"),
        ("patient", "carriage",        "open"),

        # Правильный src/dst, но другая операция
        ("control_gateway", "stop",    "invalid_op"),
        ("control_gateway", "heating", "set_speed"),       # set_speed только для cooling
        ("control_gateway", "cooling", "set_level"),       # set_level только для heating

        # Прямые обращения в обход цепочки доверия
        ("doctor",         "stop",     "emergency_stop"),  # должно идти через control_gateway
        ("doctor",         "carriage", "open"),
        ("gnss_nav",       "control_gateway", "emergency_stop"),  # GNSS недоверен
        ("ins_nav",        "control_gateway", "emergency_stop"),  # ИНС только в position_verify

        # Попытка обхода крипто-канала
        ("doctor",         "control_gateway", "forward_command"),
        ("doctor",         "command_verify",  "verify_signature"),

        # Обратное направление
        ("stop",           "control_gateway", "smooth_stop"),
        ("carriage",       "control_gateway", "open"),
    ])
    def test_denied_combinations(self, src, dst, op):
        details = {"source": src, "deliver_to": dst, "operation": op}
        assert check_operation("e", details) is False


# ============================================================
# 3. Граничные случаи
# ============================================================
class TestEdgeCases:
    def test_empty_details(self):
        assert check_operation("e", {}) is False

    def test_missing_source(self):
        assert check_operation("e", {"deliver_to": "stop", "operation": "smooth_stop"}) is False

    def test_missing_deliver_to(self):
        assert check_operation("e", {"source": "control_gateway", "operation": "smooth_stop"}) is False

    def test_missing_operation(self):
        """Без operation должно блокироваться даже валидная пара src/dst."""
        assert check_operation("e", {"source": "control_gateway", "deliver_to": "stop"}) is False

    def test_empty_string_source(self):
        assert check_operation("e", {"source": "", "deliver_to": "stop", "operation": "smooth_stop"}) is False

    def test_none_fields(self):
        assert check_operation("e", {"source": None, "deliver_to": None, "operation": None}) is False

    def test_extra_fields_ignored(self):
        """Лишние поля не должны влиять на решение."""
        details = {
            "source": "doctor", "deliver_to": "crypto_encrypt",
            "operation": "encrypt_command",
            "extra": "anything", "ts": 12345,
        }
        assert check_operation("e", details) is True


# ============================================================
# 4. Сценарии атак (ЦБ1–ЦБ11) — таблица негативных сценариев README
# ============================================================
class TestThreatScenarios:
    """
    Для каждого негативного сценария из таблицы README проверяем, что
    соответствующее опасное обращение НЕ входит в политики.
    """

    def test_threat_01_command_spoofing_CB1(self):
        """Подмена команды через канал связи (ЦБ1).

        Злоумышленник пытается отправить команду в шлюз, минуя
        crypto_decrypt и command_verify."""
        assert check_operation("t1", {
            "source": "attacker", "deliver_to": "control_gateway",
            "operation": "forward_command",
        }) is False

    def test_threat_02_control_system_compromise_CB2(self):
        """Компрометация системы управления (ЦБ2).

        Скомпрометированный шлюз пытается игнорировать сигналы остановки
        от мониторинга — переписать политику от critical_detect."""
        assert check_operation("t2", {
            "source": "control_gateway", "deliver_to": "critical_detect",
            "operation": "suppress",
        }) is False

    def test_threat_03_sensor_data_spoofing_CB2(self):
        """Подмена данных датчиков (ЦБ2).

        Поддельный «датчик» пытается отправить данные напрямую в шлюз,
        минуя sensor_verify."""
        assert check_operation("t3", {
            "source": "critical_sensors", "deliver_to": "control_gateway",
            "operation": "report_data",
        }) is False

    def test_threat_04_physio_data_interception_CB3_CB8(self):
        """Перехват физиологических данных (ЦБ3, ЦБ8).

        Попытка отправить данные пациента в обход crypto_encrypt."""
        assert check_operation("t4", {
            "source": "patient_data", "deliver_to": "doctor",
            "operation": "send_telemetry",
        }) is False

    def test_threat_05_neural_signal_spoofing_CB4_CB5_CB11(self):
        """Подмена нейронных сигналов верхних конечностей (ЦБ4, ЦБ5, ЦБ11).

        Поддельный источник нейросигнала."""
        assert check_operation("t5", {
            "source": "attacker", "deliver_to": "neuro_verify",
            "operation": "neural_signal",
        }) is False

    def test_threat_06_tactile_interpretation_CB6(self):
        """Искажение интерпретации тактильных сигналов (ЦБ6).

        Тактильный модуль не должен принимать команды напрямую от пациента
        с произвольной операцией."""
        assert check_operation("t6", {
            "source": "patient", "deliver_to": "tactile",
            "operation": "override_intensity",
        }) is False

    def test_threat_07_uncontrolled_track_CB7(self):
        """Неконтролируемое движение гусеницы (ЦБ7).

        Скомпрометированный мониторинг пытается обойти critical_detect."""
        assert check_operation("t7", {
            "source": "monitoring", "deliver_to": "control_gateway",
            "operation": "suppress_obstacle",
        }) is False

    def test_threat_08_limb_control_compromise_CB9(self):
        """Компрометация системы управления конечностей (ЦБ9).

        Прямая команда движения в обход neuro_verify."""
        assert check_operation("t8", {
            "source": "attacker", "deliver_to": "control_gateway",
            "operation": "verified_neural",
        }) is False

    def test_threat_09_geolocation_spoofing_CB10(self):
        """Подмена геолокации (ЦБ10).

        Скомпрометированный GNSS пытается отправить «правильный» статус
        зоны напрямую в шлюз, минуя position_verify."""
        assert check_operation("t9", {
            "source": "gnss_nav", "deliver_to": "control_gateway",
            "operation": "zone_status",
        }) is False

    def test_threat_10_arm_force_overrun_CB5(self):
        """Превышение силы захвата руки (ЦБ5).

        Поддельная команда на отключение ограничителя силы."""
        assert check_operation("t10", {
            "source": "attacker", "deliver_to": "leg_force_control",
            "operation": "disable_limit",
        }) is False

    def test_threat_11_uncontrolled_motion_CB4(self):
        """Неконтролируемое движение нижней конечности (ЦБ4).

        Шлюз пытается дать команду напрямую leg_force_control."""
        assert check_operation("t11", {
            "source": "control_gateway", "deliver_to": "leg_force_control",
            "operation": "set_force",
        }) is False

    def test_threat_12_carriage_lockout_CB2(self):
        """Блокировка открытия кабины (ЦБ2).

        Любая попытка ЗАКРЫТЬ кабину из аварийного цикла (от stop)
        должна быть запрещена — у stop разрешено только emergency_open."""
        assert check_operation("t12", {
            "source": "stop", "deliver_to": "carriage",
            "operation": "close",
        }) is False

    def test_threat_13_battery_data_spoofing_CB2_CB5(self):
        """Подмена данных о заряде батареи (ЦБ2, ЦБ5).

        Поддельный источник от имени critical_battery."""
        assert check_operation("t13", {
            "source": "critical_battery", "deliver_to": "control_gateway",
            "operation": "battery_full",
        }) is False

    def test_threat_14_temperature_data_spoofing_CB5(self):
        """Подмена данных о температуре (ЦБ5).

        Прямая команда нагреву в обход temperature и control_gateway."""
        assert check_operation("t14", {
            "source": "temperature", "deliver_to": "heating",
            "operation": "set_level",
        }) is False

    def test_threat_15_painful_vibration_CB5_CB6(self):
        """Болезненная вибрация (ЦБ5, ЦБ6).

        Прямая команда tactile c максимальной интенсивностью от
        непроверенного источника."""
        assert check_operation("t15", {
            "source": "doctor", "deliver_to": "tactile",
            "operation": "emit_feedback",
        }) is False


# ============================================================
# 5. Логика consumer.handle_event
# ============================================================
class TestHandleEvent:
    """
    Проверяем, что consumer корректно вызывает producer ТОЛЬКО для
    разрешённых событий. Сам Kafka не поднимаем — мокаем
    proceed_to_deliver.
    """

    @pytest.fixture
    def env(self, monkeypatch):
        consumer_mod = _load_security_module("consumer")
        delivered: list[tuple] = []

        def fake_proceed(event_id, details):
            delivered.append((event_id, details))

        monkeypatch.setattr(consumer_mod, "proceed_to_deliver", fake_proceed)
        return consumer_mod, delivered

    def test_allowed_event_is_delivered(self, env):
        consumer_mod, delivered = env
        details = {
            "source": "control_gateway",
            "deliver_to": "stop",
            "operation": "smooth_stop",
            "id": "evt-1",
        }
        consumer_mod.handle_event("evt-1", json.dumps(details))
        assert len(delivered) == 1
        assert delivered[0][0] == "evt-1"
        assert delivered[0][1]["operation"] == "smooth_stop"

    def test_denied_event_is_blocked(self, env):
        consumer_mod, delivered = env
        details = {
            "source": "hacker",
            "deliver_to": "stop",
            "operation": "reset_emergency",
            "id": "evt-2",
        }
        consumer_mod.handle_event("evt-2", json.dumps(details))
        assert delivered == []

    def test_malformed_event_no_delivery(self, env):
        """Без operation — отказ, доставка не происходит."""
        consumer_mod, delivered = env
        details = {"source": "control_gateway", "deliver_to": "stop"}
        consumer_mod.handle_event("evt-3", json.dumps(details))
        assert delivered == []

    def test_chain_of_threats_blocked(self, env):
        """Серия из 15 атак (ЦБ1–ЦБ11) — НИ ОДНО событие не должно дойти."""
        consumer_mod, delivered = env

        threats = [
            {"source": "attacker", "deliver_to": "control_gateway", "operation": "forward_command"},
            {"source": "critical_sensors", "deliver_to": "control_gateway", "operation": "report_data"},
            {"source": "patient_data", "deliver_to": "doctor", "operation": "send_telemetry"},
            {"source": "attacker", "deliver_to": "neuro_verify", "operation": "neural_signal"},
            {"source": "patient", "deliver_to": "tactile", "operation": "override_intensity"},
            {"source": "monitoring", "deliver_to": "control_gateway", "operation": "suppress_obstacle"},
            {"source": "gnss_nav", "deliver_to": "control_gateway", "operation": "zone_status"},
            {"source": "stop", "deliver_to": "carriage", "operation": "close"},
            {"source": "doctor", "deliver_to": "tactile", "operation": "emit_feedback"},
            {"source": "temperature", "deliver_to": "heating", "operation": "set_level"},
        ]
        for i, t in enumerate(threats):
            consumer_mod.handle_event(f"threat-{i}", json.dumps(t))

        assert delivered == [], f"Атаки прошли через монитор: {delivered}"

    def test_full_legitimate_chain(self, env):
        """
        Полный легитимный сценарий: врач → крипто → проверка → шлюз → стоп.
        Все 4 события должны быть доставлены.
        """
        consumer_mod, delivered = env

        chain = [
            {"source": "doctor",         "deliver_to": "crypto_encrypt",   "operation": "encrypt_command"},
            {"source": "crypto_encrypt", "deliver_to": "crypto_decrypt",   "operation": "transmit_cipher"},
            {"source": "crypto_decrypt", "deliver_to": "command_verify",   "operation": "verify_signature"},
            {"source": "command_verify", "deliver_to": "control_gateway",  "operation": "forward_command"},
            {"source": "control_gateway","deliver_to": "stop",             "operation": "smooth_stop"},
        ]
        for i, ev in enumerate(chain):
            consumer_mod.handle_event(f"link-{i}", json.dumps(ev))

        assert len(delivered) == 5
        ops = [d[1]["operation"] for d in delivered]
        assert ops == [
            "encrypt_command", "transmit_cipher", "verify_signature",
            "forward_command", "smooth_stop",
        ]
