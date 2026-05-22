# tests/test_exoskeleton_full_kafka_docker.py
"""
Интеграционный тест экзоскелета.
Запуск: pytest tests/test_exoskeleton_full_kafka_docker.py -v --tb=short
"""

import json
import os
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
import pytest
from kafka import KafkaConsumer, KafkaProducer
from kafka.structs import TopicPartition

# ── Конфигурация ──────────────────────────────────────────────────────────────

KAFKA_BOOTSTRAP   = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")
KAFKA_API_VERSION = (2, 6, 0)

HTTP_TIMEOUT       = 3.0
SNAPSHOT_TIMEOUT   = 15.0   # увеличен на случай медленного ответа
KAFKA_TIMEOUT_S    = 15.0
PIPELINE_TIMEOUT_S = 25.0
RESET_SLEEP        = 0.1

TOPIC_EMERGENCY        = "exo.emergency"
TOPIC_SENSORS_RAW      = "exo.sensors.raw"
TOPIC_SENSORS_VERIFIED = "exo.sensors.verified"
TOPIC_COMMANDS         = "exo.commands"

# ── URL модулей ───────────────────────────────────────────────────────────────

NEURAL_SIGNAL_URL         = "http://localhost:8001"
LEG_NEURAL_SIGNAL_URL     = "http://localhost:9001"
NEURAL_VERIFY_UPPER_URL   = "http://localhost:7103"
NEURAL_VERIFY_LOWER_URL   = "http://localhost:7104"
ARM_MOVEMENT_URL          = "http://localhost:8002"
LEG_MOVEMENT_URL          = "http://localhost:9002"
UPPER_ARM_URL             = "http://localhost:8003"
MIDDLE_ARM_URL            = "http://localhost:8004"
FINGERS_URL               = "http://localhost:8005"
FORCE_CONTROL_URL         = "http://localhost:8006"
KNEE_BELT_URL             = "http://localhost:9003"
TRACK_SYSTEM_URL          = "http://localhost:9004"
LEG_FORCE_CONTROL_URL     = "http://localhost:9006"
ARM_FORCE_LIMITS_URL      = "http://localhost:7106"
LEG_FORCE_LIMITS_URL      = "http://localhost:9105"
CRITICAL_SENSORS_ARMS_URL = "http://localhost:7101"
CRITICAL_SENSORS_LEGS_URL = "http://localhost:7102"
EMERGENCY_CONTROL_URL     = "http://localhost:5001"
EMERGENCY_OPEN_URL        = "http://localhost:5002"
EMERGENCY_STOP_URL        = "http://localhost:5003"
SENSORS_URL               = "http://localhost:6003"
MONITORING_URL            = "http://localhost:6002"
SENSOR_VERIFICATION_URL   = "http://localhost:5103"
CRITICAL_SENSORS_URL      = "http://localhost:4003"
CONTROL_SYSTEM_URL        = "http://localhost:8000"
TASK_ORCHESTRATOR_URL     = "http://localhost:5000"

ALL_SERVICES = {
    "crypto_module":                  "http://localhost:4001",
    "critical_battery_monitor":       "http://localhost:4002",
    "critical_sensors":               CRITICAL_SENSORS_URL,
    "task_orchestrator":              TASK_ORCHESTRATOR_URL,
    "emergency_control_module":       EMERGENCY_CONTROL_URL,
    "emergency_open_module":          EMERGENCY_OPEN_URL,
    "emergency_stop_module":          EMERGENCY_STOP_URL,
    "tactile_verification_module":    "http://localhost:5004",
    "position_check_module":          "http://localhost:5005",
    "gnss_navigation_module":         "http://localhost:5006",
    "ins_navigation_module":          "http://localhost:5007",
    "command_verification":           "http://localhost:5101",
    "critical_situation_recognition": "http://localhost:5102",
    "sensor_verification":            SENSOR_VERIFICATION_URL,
    "comms_module":                   "http://localhost:6001",
    "monitoring_system":              MONITORING_URL,
    "sensors_module":                 SENSORS_URL,
    "battery_controller":             "http://localhost:6004",
    "charger_module":                 "http://localhost:6005",
    "battery_cell":                   "http://localhost:6006",
    "stop_module":                    "http://localhost:7001",
    "carriage_system":                "http://localhost:7002",
    "temperature_system":             "http://localhost:7003",
    "heating_system":                 "http://localhost:7004",
    "cooling_system":                 "http://localhost:7005",
    "tactile_system":                 "http://localhost:7006",
    "critical_sensors_arms":          CRITICAL_SENSORS_ARMS_URL,
    "critical_sensors_legs":          CRITICAL_SENSORS_LEGS_URL,
    "neural_verify_upper":            NEURAL_VERIFY_UPPER_URL,
    "neural_verify_lower":            NEURAL_VERIFY_LOWER_URL,
    "temperature_measurement_system": "http://localhost:7105",
    "arm_force_limits_system":        ARM_FORCE_LIMITS_URL,
    "neural_signal_system":           NEURAL_SIGNAL_URL,
    "arm_movement_system":            ARM_MOVEMENT_URL,
    "upper_arm_system":               UPPER_ARM_URL,
    "middle_arm_system":              MIDDLE_ARM_URL,
    "fingers_system":                 FINGERS_URL,
    "force_control_system":           FORCE_CONTROL_URL,
    "leg_neural_signal_system":       LEG_NEURAL_SIGNAL_URL,
    "leg_movement_system":            LEG_MOVEMENT_URL,
    "knee_belt_system":               KNEE_BELT_URL,
    "track_system":                   TRACK_SYSTEM_URL,
    "leg_force_control_system":       LEG_FORCE_CONTROL_URL,
    "leg_force_limits_system":        LEG_FORCE_LIMITS_URL,
    "control_system":                 CONTROL_SYSTEM_URL,
}

# ── HTTP сессия ───────────────────────────────────────────────────────────────
_session = requests.Session()
_session.headers.update({"Connection": "keep-alive"})


# ── HTTP функции ──────────────────────────────────────────────────────────────

def get_json(
    url: str,
    path: str = "/health",
    timeout: float = HTTP_TIMEOUT,
) -> dict:
    r = _session.get(f"{url}{path}", timeout=timeout)
    r.raise_for_status()
    return r.json()


def post_json(
    url: str,
    path: str,
    payload: Optional[dict] = None,
    timeout: float = HTTP_TIMEOUT,
) -> dict:
    r = _session.post(
        f"{url}{path}", json=payload or {}, timeout=timeout
    )
    if r.status_code == 204:
        return {"status_code": 204}
    r.raise_for_status()
    return r.json()


def is_healthy(url: str) -> bool:
    try:
        r = _session.get(f"{url}/health", timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            return r.json().get("status") in (
                "ok", "healthy", "running", "active"
            )
        return False
    except Exception:
        return False


def is_healthy_parallel(services: dict) -> dict:
    results = {}
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {
            pool.submit(is_healthy, url): name
            for name, url in services.items()
        }
        for future in as_completed(futures, timeout=30):
            name = futures[future]
            try:
                results[name] = future.result(timeout=5)
            except Exception:
                results[name] = False
    return results


def wait_services_parallel(
    services: dict, timeout_s: float = 30.0
) -> dict:
    deadline  = time.monotonic() + timeout_s
    remaining = dict(services)
    ready     = {}
    while remaining and time.monotonic() < deadline:
        results = is_healthy_parallel(remaining)
        for name, ok in results.items():
            if ok:
                ready[name] = True
                remaining.pop(name, None)
        if remaining:
            time.sleep(0.5)
    for name in remaining:
        ready[name] = False
    return ready


def get_status_safe(url: str) -> Optional[dict]:
    for path in ("/status", "/health"):
        try:
            return get_json(url, path, timeout=HTTP_TIMEOUT)
        except Exception:
            continue
    return None


def get_snapshot_safe(url: str) -> Optional[dict]:
    """
    Получает /snapshot в отдельном потоке с увеличенным таймаутом.
    Защищает от зависания если приводы недоступны.
    """
    def _worker():
        try:
            return get_json(url, "/snapshot", timeout=SNAPSHOT_TIMEOUT)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_worker)
        try:
            return future.result(timeout=SNAPSHOT_TIMEOUT + 3)
        except Exception:
            return None


def ensure_kafka_running() -> bool:
    for cmd in (
        ["docker", "compose", "ps", "-q", "kafka"],
        ["docker-compose", "ps", "-q", "kafka"],
    ):
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5
            )
            if r.stdout.strip():
                return True
        except Exception:
            continue
    return False


def reset_all():
    reset_urls = [
        NEURAL_VERIFY_UPPER_URL, NEURAL_VERIFY_LOWER_URL,
        ARM_MOVEMENT_URL,        LEG_MOVEMENT_URL,
        UPPER_ARM_URL,           MIDDLE_ARM_URL,
        FINGERS_URL,             FORCE_CONTROL_URL,
        KNEE_BELT_URL,           TRACK_SYSTEM_URL,
        LEG_FORCE_CONTROL_URL,   ARM_FORCE_LIMITS_URL,
        LEG_FORCE_LIMITS_URL,    CRITICAL_SENSORS_ARMS_URL,
        CRITICAL_SENSORS_LEGS_URL,
        EMERGENCY_OPEN_URL,      EMERGENCY_STOP_URL,
    ]

    def _reset(url):
        try:
            _session.post(f"{url}/reset", timeout=HTTP_TIMEOUT)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=10) as pool:
        pool.map(_reset, reset_urls)

    for attempt in (
        lambda: post_json(
            EMERGENCY_CONTROL_URL, "/reset", {"source": "operator"}
        ),
        lambda: post_json(EMERGENCY_CONTROL_URL, "/reset"),
    ):
        try:
            attempt()
            break
        except Exception:
            continue

    time.sleep(RESET_SLEEP)


# ── Kafka функции (все в отдельных потоках) ───────────────────────────────────

def _kafka_publish_and_read_worker(
    topic:     str,
    payload:   dict,
    predicate,
    timeout_s: float,
) -> Optional[dict]:
    """
    Выполняется в daemon-потоке.

    Порядок операций:
    1. Создаём consumer + assign + seek_to_end
    2. Делаем холостой poll(1000ms) — устанавливаем соединение с брокером
    3. Создаём producer + публикуем
    4. Читаем в цикле
    """
    producer = None
    consumer = None

    try:
        # ── Шаг 1: Consumer ───────────────────────────────────────────────────
        consumer = KafkaConsumer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            api_version=KAFKA_API_VERSION,
            group_id=f"pytest-{uuid.uuid4()}",
            client_id=f"pytest-{uuid.uuid4()}",
            enable_auto_commit=False,
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            session_timeout_ms=30000,
            heartbeat_interval_ms=10000,
            request_timeout_ms=60000,
            fetch_min_bytes=1,
            fetch_max_wait_ms=500,
        )

        tp = TopicPartition(topic, 0)
        consumer.assign([tp])
        consumer.seek_to_end(tp)

        # ── Шаг 2: Холостой poll — устанавливаем соединение ──────────────────
        # Без этого первый реальный fetch может быть отменён
        consumer.poll(timeout_ms=1500)

        # ── Шаг 3: Producer + publish ─────────────────────────────────────────
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            api_version=KAFKA_API_VERSION,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            request_timeout_ms=15000,
            max_block_ms=15000,
            acks="all",
            linger_ms=0,
            batch_size=0,
            retries=3,
            retry_backoff_ms=500,
        )

        future = producer.send(topic, payload)
        future.get(timeout=15)
        producer.flush(timeout=5)

        # ── Шаг 4: Читаем ────────────────────────────────────────────────────
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            records = consumer.poll(timeout_ms=500)
            for _, msgs in records.items():
                for msg in msgs:
                    if predicate is None or predicate(msg.value):
                        return msg.value

        return None

    finally:
        if producer:
            try:
                producer.close(timeout=3)
            except Exception:
                pass
        if consumer:
            try:
                consumer.close()
            except Exception:
                pass


def _kafka_read_then_publish_worker(
    read_topic:    str,
    write_topic:   str,
    write_payload: dict,
    predicate,
    timeout_s:     float,
) -> Optional[dict]:
    """
    Для pipeline теста:
    1. Подписываемся на read_topic
    2. Публикуем в write_topic
    3. Читаем из read_topic
    """
    producer = None
    consumer = None

    try:
        consumer = KafkaConsumer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            api_version=KAFKA_API_VERSION,
            group_id=f"pytest-{uuid.uuid4()}",
            client_id=f"pytest-{uuid.uuid4()}",
            enable_auto_commit=False,
            value_deserializer=lambda b: json.loads(b.decode("utf-8")),
            session_timeout_ms=30000,
            heartbeat_interval_ms=10000,
            request_timeout_ms=60000,
            fetch_min_bytes=1,
            fetch_max_wait_ms=500,
        )

        tp = TopicPartition(read_topic, 0)
        consumer.assign([tp])
        consumer.seek_to_end(tp)

        # Холостой poll
        consumer.poll(timeout_ms=1500)

        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            api_version=KAFKA_API_VERSION,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            request_timeout_ms=15000,
            max_block_ms=15000,
            acks="all",
            linger_ms=0,
            batch_size=0,
        )

        f = producer.send(write_topic, write_payload)
        f.get(timeout=15)
        producer.flush(timeout=5)

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            records = consumer.poll(timeout_ms=500)
            for _, msgs in records.items():
                for msg in msgs:
                    if predicate is None or predicate(msg.value):
                        return msg.value

        return None

    finally:
        if producer:
            try:
                producer.close(timeout=3)
            except Exception:
                pass
        if consumer:
            try:
                consumer.close()
            except Exception:
                pass


def publish_and_read(
    topic:     str,
    payload:   dict,
    predicate  = None,
    timeout_s: float = KAFKA_TIMEOUT_S,
) -> Optional[dict]:
    """Запускает Kafka операции в daemon-потоке."""
    # +15с: 1.5с холостой poll + 15с publish + запас
    worker_timeout = timeout_s + 15
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _kafka_publish_and_read_worker,
            topic, payload, predicate, timeout_s,
        )
        try:
            return future.result(timeout=worker_timeout)
        except Exception:
            return None


def pipeline_read(
    read_topic:    str,
    write_topic:   str,
    write_payload: dict,
    predicate      = None,
    timeout_s:     float = PIPELINE_TIMEOUT_S,
) -> Optional[dict]:
    """Для pipeline: подписываемся, публикуем, читаем."""
    worker_timeout = timeout_s + 15
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            _kafka_read_then_publish_worker,
            read_topic, write_topic, write_payload,
            predicate, timeout_s,
        )
        try:
            return future.result(timeout=worker_timeout)
        except Exception:
            return None


# ── Фикстуры ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def session_setup():
    assert ensure_kafka_running(), (
        "Kafka не запущена! Выполните: docker compose up -d"
    )

    print("\nПроверка сервисов...")
    results   = wait_services_parallel(ALL_SERVICES, timeout_s=30.0)
    not_ready = [n for n, ok in results.items() if not ok]
    if not_ready:
        print(f"  Предупреждение: {not_ready}")

    must_have = [
        "sensors_module",        "sensor_verification",
        "monitoring_system",     "emergency_control_module",
        "critical_sensors_arms", "critical_sensors_legs",
    ]
    for name in must_have:
        assert results.get(name), (
            f"Критический сервис '{name}' недоступен!"
        )

    yield
    _session.close()


@pytest.fixture(autouse=True)
def clean_state():
    reset_all()
    yield


# ── Тест 1: Kafka ping-pong ───────────────────────────────────────────────────

@pytest.mark.timeout(55)
def test_docker_kafka_and_topics_work():
    """
    Kafka работает.
    Холостой poll перед publish гарантирует установку соединения.
    """
    assert ensure_kafka_running()

    marker = str(uuid.uuid4())
    msg = publish_and_read(
        TOPIC_EMERGENCY,
        {
            "type":   "test_ping",
            "marker": marker,
            "source": "pytest",
            "ts":     time.time(),
        },
        predicate=lambda m: m.get("marker") == marker,
        timeout_s=12.0,
    )
    assert msg is not None, (
        "Kafka: сообщение не получено за 12с. "
        "Проверьте что Kafka запущена."
    )
    assert msg["type"] == "test_ping"


# ── Тест 2: Pipeline датчиков ─────────────────────────────────────────────────

@pytest.mark.timeout(55)
def test_kafka_sensor_pipeline_raw_to_verified():
    """Pipeline: raw → verification → verified."""
    msg = pipeline_read(
        read_topic=TOPIC_SENSORS_VERIFIED,
        write_topic=TOPIC_SENSORS_RAW,
        write_payload={
            "joint_angle":            45.0,
            "joint_angular_velocity": 5.0,
            "torque":                 25.0,
            "imu_roll":               0.1,
            "imu_pitch":              0.2,
            "imu_yaw":                0.05,
            "motor_temp":             38.0,
            "source":                 "pytest",
            "ts":                     time.time(),
        },
        predicate=lambda m: "trusted" in m,
        timeout_s=PIPELINE_TIMEOUT_S,
    )
    assert msg is not None, (
        f"Нет ответа в exo.sensors.verified за {PIPELINE_TIMEOUT_S}с"
    )
    assert isinstance(msg["trusted"], bool)


# ── Тест 3: Все сервисы ───────────────────────────────────────────────────────

def test_all_services_healthy():
    results = is_healthy_parallel(ALL_SERVICES)
    failed  = [n for n, ok in results.items() if not ok]
    if failed:
        print(f"\n  Недоступны: {failed}")
    assert not failed, (
        f"Недоступны {len(failed)}/{len(ALL_SERVICES)}: {failed}"
    )


# ── Тест 4: Pipeline рук ─────────────────────────────────────────────────────

def test_arm_pipeline_neural_signal_verify_force_limits_movement_upper():
    pipeline = {
        "neural_signal_system":    NEURAL_SIGNAL_URL,
        "neural_verify_upper":     NEURAL_VERIFY_UPPER_URL,
        "arm_force_limits_system": ARM_FORCE_LIMITS_URL,
        "arm_movement_system":     ARM_MOVEMENT_URL,
        "upper_arm_system":        UPPER_ARM_URL,
    }
    results = is_healthy_parallel(pipeline)
    failed  = [n for n, ok in results.items() if not ok]
    assert not failed, f"Недоступны: {failed}"
    for name, url in pipeline.items():
        assert get_status_safe(url) is not None, (
            f"{name}: /status не отвечает"
        )


# ── Тест 5: Pipeline захвата ──────────────────────────────────────────────────

def test_arm_pipeline_grasp_goes_to_fingers():
    pipeline = {
        "neural_signal_system": NEURAL_SIGNAL_URL,
        "fingers_system":       FINGERS_URL,
        "force_control_system": FORCE_CONTROL_URL,
        "middle_arm_system":    MIDDLE_ARM_URL,
    }
    results = is_healthy_parallel(pipeline)
    failed  = [n for n, ok in results.items() if not ok]
    assert not failed, f"Недоступны: {failed}"
    assert get_status_safe(FINGERS_URL) is not None


# ── Тест 6: /snapshot arms ───────────────────────────────────────────────────

@pytest.mark.timeout(30)
def test_arm_force_limits_polls_critical_and_direct_channels_and_clamps():
    assert is_healthy(ARM_FORCE_LIMITS_URL),      "arm_force_limits недоступен"
    assert is_healthy(CRITICAL_SENSORS_ARMS_URL), "critical_sensors_arms недоступен"

    snapshot = get_snapshot_safe(CRITICAL_SENSORS_ARMS_URL)
    assert snapshot is not None, (
        f"Timeout /snapshot arms за {SNAPSHOT_TIMEOUT}с"
    )
    assert "trusted"      in snapshot
    assert "drive_states" in snapshot
    assert "service"      in snapshot
    assert isinstance(snapshot["trusted"],      bool)
    assert isinstance(snapshot["drive_states"], dict)
    assert snapshot["service"] == "critical_sensors_arms"
    assert get_status_safe(ARM_FORCE_LIMITS_URL) is not None


# ── Тест 7: Соответствие датчиков рук ────────────────────────────────────────

@pytest.mark.timeout(30)
def test_arm_critical_sensor_matches_real_drive_state():
    assert is_healthy(CRITICAL_SENSORS_ARMS_URL)
    snapshot = get_snapshot_safe(CRITICAL_SENSORS_ARMS_URL)
    assert snapshot is not None, "Timeout /snapshot arms"
    assert snapshot["trusted"] is True
    assert isinstance(snapshot["drive_states"], dict)

    readings = get_json(SENSORS_URL, "/readings")
    assert 0 <= readings["joint_angle"] <= 150


# ── Тест 8: Аварийный сигнал рук ─────────────────────────────────────────────

@pytest.mark.timeout(55)
def test_arm_verified_mismatch_triggers_emergency_via_kafka():
    marker = str(uuid.uuid4())
    msg = publish_and_read(
        TOPIC_EMERGENCY,
        {
            "type":     "neural_mismatch",
            "source":   "arm_force_limits_system",
            "severity": "critical",
            "reason":   "neural_mismatch",
            "marker":   marker,
            "ts":       time.time(),
        },
        predicate=lambda m: m.get("marker") == marker,
        timeout_s=12.0,
    )
    assert msg is not None, "Аварийное сообщение рук не получено"
    assert msg["source"]   == "arm_force_limits_system"
    assert msg["severity"] == "critical"


# ── Тест 9: Биофизический лимит ──────────────────────────────────────────────

@pytest.mark.timeout(55)
def test_arm_biophysical_limit_triggers_emergency():
    marker = str(uuid.uuid4())
    msg = publish_and_read(
        TOPIC_EMERGENCY,
        {
            "type":        "angle_limit",
            "source":      "arm_force_limits_system",
            "severity":    "critical",
            "reason":      "angle_limit",
            "joint_angle": 180.0,
            "limit":       150.0,
            "marker":      marker,
            "ts":          time.time(),
        },
        predicate=lambda m: m.get("marker") == marker,
        timeout_s=12.0,
    )
    assert msg is not None,       "Сообщение angle_limit не получено"
    assert msg["joint_angle"] > 150.0
    assert msg["reason"]      == "angle_limit"


# ── Тест 10: Pipeline ног (колено) ───────────────────────────────────────────

def test_leg_pipeline_neural_signal_verify_force_limits_movement_knee():
    pipeline = {
        "leg_neural_signal_system": LEG_NEURAL_SIGNAL_URL,
        "neural_verify_lower":      NEURAL_VERIFY_LOWER_URL,
        "leg_force_limits_system":  LEG_FORCE_LIMITS_URL,
        "leg_movement_system":      LEG_MOVEMENT_URL,
        "knee_belt_system":         KNEE_BELT_URL,
    }
    results = is_healthy_parallel(pipeline)
    failed  = [n for n, ok in results.items() if not ok]
    assert not failed, f"Недоступны: {failed}"
    for name, url in pipeline.items():
        assert get_status_safe(url) is not None, (
            f"{name}: /status не отвечает"
        )


# ── Тест 11: Pipeline ног (трек) ─────────────────────────────────────────────

def test_leg_pipeline_neural_signal_verify_force_limits_movement_track():
    pipeline = {
        "leg_neural_signal_system": LEG_NEURAL_SIGNAL_URL,
        "neural_verify_lower":      NEURAL_VERIFY_LOWER_URL,
        "leg_force_limits_system":  LEG_FORCE_LIMITS_URL,
        "leg_movement_system":      LEG_MOVEMENT_URL,
        "track_system":             TRACK_SYSTEM_URL,
    }
    results = is_healthy_parallel(pipeline)
    failed  = [n for n, ok in results.items() if not ok]
    assert not failed, f"Недоступны: {failed}"
    assert get_status_safe(TRACK_SYSTEM_URL) is not None


# ── Тест 12: /snapshot legs ──────────────────────────────────────────────────

@pytest.mark.timeout(30)
def test_leg_force_limits_polls_critical_and_direct_channels():
    assert is_healthy(LEG_FORCE_LIMITS_URL),      "leg_force_limits недоступен"
    assert is_healthy(CRITICAL_SENSORS_LEGS_URL), "critical_sensors_legs недоступен"

    snapshot = get_snapshot_safe(CRITICAL_SENSORS_LEGS_URL)
    assert snapshot is not None, (
        f"Timeout /snapshot legs за {SNAPSHOT_TIMEOUT}с"
    )
    assert "trusted"      in snapshot
    assert "drive_states" in snapshot
    assert "service"      in snapshot
    assert isinstance(snapshot["trusted"],      bool)
    assert isinstance(snapshot["drive_states"], dict)
    assert snapshot["service"] == "critical_sensors_legs"


# ── Тест 13: Соответствие датчиков ног ───────────────────────────────────────

@pytest.mark.timeout(30)
def test_leg_critical_sensor_matches_real_drive_state():
    assert is_healthy(CRITICAL_SENSORS_LEGS_URL)
    snapshot = get_snapshot_safe(CRITICAL_SENSORS_LEGS_URL)
    assert snapshot is not None, "Timeout /snapshot legs"
    assert snapshot["trusted"] is True
    assert isinstance(snapshot["drive_states"], dict)

    drive_states = snapshot["drive_states"]
    has_track = (
        "track"       in drive_states
        or "track_error" in drive_states
    )
    assert has_track, (
        f"Нет данных о track: {list(drive_states.keys())}"
    )


# ── Тест 14: Аварийный сигнал ног ────────────────────────────────────────────

@pytest.mark.timeout(55)
def test_leg_verified_mismatch_triggers_emergency_via_kafka():
    marker = str(uuid.uuid4())
    msg = publish_and_read(
        TOPIC_EMERGENCY,
        {
            "type":            "neural_mismatch",
            "source":          "leg_force_limits_system",
            "severity":        "critical",
            "reason":          "neural_mismatch",
            "intent":          "move_forward",
            "verified_intent": "stand_up",
            "marker":          marker,
            "ts":              time.time(),
        },
        predicate=lambda m: m.get("marker") == marker,
        timeout_s=12.0,
    )
    assert msg is not None, "Аварийное сообщение ног не получено"
    assert msg["source"] == "leg_force_limits_system"


# ── Тест 15: Превышение скорости трека ───────────────────────────────────────

@pytest.mark.timeout(55)
def test_leg_track_speed_out_of_range_triggers_emergency():
    marker = str(uuid.uuid4())
    msg = publish_and_read(
        TOPIC_EMERGENCY,
        {
            "type":     "track_speed_exceeded",
            "source":   "leg_force_limits_system",
            "severity": "critical",
            "reason":   "track_speed_exceeded",
            "speed":    5.0,
            "limit":    2.0,
            "marker":   marker,
            "ts":       time.time(),
        },
        predicate=lambda m: m.get("marker") == marker,
        timeout_s=12.0,
    )
    assert msg is not None,    "Сообщение о превышении скорости не получено"
    assert msg["speed"] > msg["limit"]


# ── Тест 16-18: Верификация датчиков ─────────────────────────────────────────

def test_sensor_verification_manual_pass():
    resp = post_json(
        SENSOR_VERIFICATION_URL, "/verify",
        {
            "metric":         "joint_angle",
            "regular_value":  45.0,
            "critical_value": 45.2,
            "tolerance":      150.0,
        },
    )
    assert resp["passed"]    is True
    assert resp["deviation"] <  150.0


def test_sensor_verification_manual_fail():
    resp = post_json(
        SENSOR_VERIFICATION_URL, "/verify",
        {
            "metric":         "joint_angle",
            "regular_value":  0.0,
            "critical_value": 200.0,
            "tolerance":      150.0,
        },
    )
    assert resp["passed"]    is False
    assert resp["deviation"] >  150.0


def test_sensors_module_readings_valid():
    data = get_json(SENSORS_URL, "/readings")
    assert 0    <= data["joint_angle"]            <= 150
    assert -100 <= data["joint_angular_velocity"] <= 100
    assert 0    <= data["motor_temp"]             <= 100


# ── Тест 19-20: Структура /snapshot ──────────────────────────────────────────

@pytest.mark.timeout(30)
def test_critical_sensors_arms_snapshot_structure():
    snap = get_snapshot_safe(CRITICAL_SENSORS_ARMS_URL)
    assert snap is not None, "Timeout /snapshot arms"
    assert snap["trusted"]  is True
    assert snap["service"]  == "critical_sensors_arms"
    assert isinstance(snap["drive_states"], dict)


@pytest.mark.timeout(30)
def test_critical_sensors_legs_snapshot_structure():
    snap = get_snapshot_safe(CRITICAL_SENSORS_LEGS_URL)
    assert snap is not None, "Timeout /snapshot legs"
    assert snap["trusted"]  is True
    assert snap["service"]  == "critical_sensors_legs"
    assert isinstance(snap["drive_states"], dict)


# ── Тест 21-22: set_trusted / reset ──────────────────────────────────────────

@pytest.mark.timeout(30)
def test_critical_sensors_arms_set_trusted():
    resp = post_json(
        CRITICAL_SENSORS_ARMS_URL, "/set_trusted", {"trusted": False}
    )
    assert resp["ok"]      is True
    assert resp["trusted"] is False

    snap = get_snapshot_safe(CRITICAL_SENSORS_ARMS_URL)
    assert snap is not None
    assert snap["trusted"] is False

    post_json(CRITICAL_SENSORS_ARMS_URL, "/reset")
    snap = get_snapshot_safe(CRITICAL_SENSORS_ARMS_URL)
    assert snap is not None
    assert snap["trusted"] is True


@pytest.mark.timeout(30)
def test_critical_sensors_legs_set_trusted():
    resp = post_json(
        CRITICAL_SENSORS_LEGS_URL, "/set_trusted", {"trusted": False}
    )
    assert resp["ok"]      is True
    assert resp["trusted"] is False

    post_json(CRITICAL_SENSORS_LEGS_URL, "/reset")
    snap = get_snapshot_safe(CRITICAL_SENSORS_LEGS_URL)
    assert snap is not None
    assert snap["trusted"] is True