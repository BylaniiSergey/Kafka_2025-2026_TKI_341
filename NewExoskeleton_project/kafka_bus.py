"""
Kafka Event Bus — финальная версия для Windows + Docker Desktop
"""

import os
import json
import time
import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger("kafka_bus")

# 127.0.0.1 вместо localhost — принудительный IPv4
KAFKA_BOOTSTRAP = os.getenv('KAFKA_BOOTSTRAP_SERVERS', '127.0.0.1:9092')
KAFKA_ENABLED = os.getenv('KAFKA_ENABLED', 'true').lower() == 'true'
KAFKA_API_VERSION = (2, 6, 0)

TOPIC_EMERGENCY = 'exo.emergency'
TOPIC_SENSORS_RAW = 'exo.sensors.raw'
TOPIC_SENSORS_VERIFIED = 'exo.sensors.verified'
TOPIC_COMMANDS = 'exo.commands'
TOPIC_TELEMETRY = 'exo.telemetry'
TOPIC_ALARMS = 'exo.alarms'

# Локальная шина (fallback)
_local_handlers: dict[str, list] = {}
_local_lock = threading.Lock()


def _local_publish(topic: str, payload: dict) -> bool:
    with _local_lock:
        handlers = list(_local_handlers.get(topic, []))
    for h in handlers:
        try:
            threading.Thread(target=h, args=(payload,), daemon=True).start()
        except Exception as e:
            logger.error("Local handler error on %s: %s", topic, e)
    return True


def _local_subscribe(topic: str, handler: Callable):
    with _local_lock:
        _local_handlers.setdefault(topic, []).append(handler)
    logger.info("Local subscribe: %s", topic)


def _make_producer(client_id: str):
    from kafka import KafkaProducer
    return KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        client_id=client_id,
        value_serializer=lambda v: json.dumps(v).encode('utf-8'),
        api_version=KAFKA_API_VERSION,

        # Надёжность
        acks='all',
        retries=10,
        retry_backoff_ms=2000,

        # Батчинг
        linger_ms=100,
        batch_size=65536,
        buffer_memory=67108864,

        # Таймауты (только поддерживаемые kafka-python)
        request_timeout_ms=60000,
        max_block_ms=30000,
        # УБРАНО: delivery_timeout_ms — не поддерживается kafka-python

        # Стабильность соединения
        connections_max_idle_ms=900000,
        reconnect_backoff_ms=2000,
        reconnect_backoff_max_ms=30000,
    )


def _make_consumer(topic: str, group_id: str, client_id: str):
    from kafka import KafkaConsumer
    return KafkaConsumer(
        topic,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=group_id,
        client_id=client_id,
        value_deserializer=lambda b: json.loads(b.decode('utf-8')),
        api_version=KAFKA_API_VERSION,

        auto_offset_reset='latest',
        enable_auto_commit=False,

        # Таймауты сессии
        session_timeout_ms=120000,
        heartbeat_interval_ms=30000,
        max_poll_interval_ms=600000,
        max_poll_records=5,

        # Сетевые таймауты
        request_timeout_ms=130000,
        connections_max_idle_ms=900000,
        reconnect_backoff_ms=2000,
        reconnect_backoff_max_ms=30000,

        # Fetch
        fetch_min_bytes=1,
        fetch_max_wait_ms=1000,
    )


class EventBus:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self._producer = None
        self._threads: list[threading.Thread] = []
        self._stop_events: list[threading.Event] = []
        self._lock = threading.Lock()

        if KAFKA_ENABLED:
            self._init_producer()
        else:
            logger.info("Kafka disabled for '%s', using local bus", client_id)

    def _init_producer(self):
        for attempt in range(15):
            try:
                self._producer = _make_producer(self.client_id)
                logger.info(
                    "Kafka producer started: client_id=%s servers=%s",
                    self.client_id, KAFKA_BOOTSTRAP,
                )
                return
            except ImportError:
                logger.warning("kafka-python not installed")
                return
            except Exception as e:
                wait = min(2 ** attempt, 30)
                logger.warning(
                    "Kafka not available, retry in %ds (%d/15): %s",
                    wait, attempt + 1, e,
                )
                time.sleep(wait)

        logger.error(
            "Kafka producer failed after 15 attempts for '%s'",
            self.client_id,
        )

    def publish(self, topic: str, payload: dict) -> bool:
        payload = dict(payload)
        payload.setdefault('source', self.client_id)
        payload.setdefault('ts', time.time())

        if not KAFKA_ENABLED or self._producer is None:
            return _local_publish(topic, payload)

        try:
            future = self._producer.send(topic, payload)
            future.get(timeout=15)
            return True
        except Exception as e:
            logger.error("Kafka publish to '%s' failed: %s", topic, e)
            return _local_publish(topic, payload)

    def subscribe(
        self,
        topic: str,
        handler: Callable[[dict], None],
        group_id: Optional[str] = None,
    ):
        if not KAFKA_ENABLED:
            _local_subscribe(topic, handler)
            return

        if group_id is None:
            group_id = f"{self.client_id}-group"

        stop_event = threading.Event()
        self._stop_events.append(stop_event)

        thread = threading.Thread(
            target=self._consumer_loop,
            args=(topic, handler, group_id, stop_event),
            name=f"kafka-{topic}",
            daemon=True,
        )
        thread.start()
        self._threads.append(thread)

    def _consumer_loop(
        self,
        topic: str,
        handler: Callable,
        group_id: str,
        stop_event: threading.Event,
    ):
        consumer = None
        attempt = 0

        while not stop_event.is_set():
            if consumer is None:
                try:
                    consumer = _make_consumer(
                        topic, group_id, f"{self.client_id}-consumer"
                    )
                    logger.info(
                        "Subscribed to '%s' (group=%s)", topic, group_id
                    )
                    attempt = 0
                    time.sleep(2.0)
                except ImportError:
                    _local_subscribe(topic, handler)
                    return
                except Exception as e:
                    wait = min(2 ** attempt, 30)
                    logger.warning(
                        "Consumer retry in %ds for '%s': %s", wait, topic, e
                    )
                    time.sleep(wait)
                    attempt += 1
                    continue

            try:
                records = consumer.poll(timeout_ms=1000)
                for _, messages in records.items():
                    for msg in messages:
                        if stop_event.is_set():
                            break
                        try:
                            handler(msg.value)
                        except Exception as e:
                            logger.exception("Handler error: %s", e)

                if records:
                    try:
                        consumer.commit()
                    except Exception as e:
                        logger.debug("Commit warning: %s", e)

            except Exception as e:
                err = str(e)
                if any(x in err for x in (
                    'WinError', 'Invalid file', 'socket', 'Connection',
                    'NodeNotReady', 'NoBrokers', 'Timeout', 'CommitFailed',
                    'RequestTimedOut', 'coordinator',
                )):
                    logger.warning(
                        "Consumer reconnecting for '%s': %s", topic, e
                    )
                    try:
                        consumer.close()
                    except Exception:
                        pass
                    consumer = None
                    time.sleep(5.0)
                else:
                    logger.error("Consumer error: %s", e)
                    time.sleep(2.0)

        if consumer:
            try:
                consumer.close()
            except Exception:
                pass

    def close(self):
        for event in self._stop_events:
            event.set()
        for thread in self._threads:
            thread.join(timeout=3.0)
        with self._lock:
            if self._producer:
                try:
                    self._producer.flush(timeout=3)
                    self._producer.close(timeout=3)
                except Exception:
                    pass
                self._producer = None