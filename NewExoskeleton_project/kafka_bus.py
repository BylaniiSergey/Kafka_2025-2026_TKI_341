"""
Общая шина обмена событиями через Kafka.

Если Kafka отключена через:
    KAFKA_ENABLED=false

или библиотека/брокер недоступны — модули НЕ падают.
Публикации просто не уходят, подписки пропускаются.
"""

import os
import json
import time
import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092')
KAFKA_ENABLED = os.getenv('KAFKA_ENABLED', 'true').lower() == 'true'

TOPIC_EMERGENCY = 'exo.emergency'
TOPIC_SENSORS_RAW = 'exo.sensors.raw'
TOPIC_SENSORS_VERIFIED = 'exo.sensors.verified'
TOPIC_COMMANDS = 'exo.commands'
TOPIC_TELEMETRY = 'exo.telemetry'
TOPIC_ALARMS = 'exo.alarms'


class EventBus:
    """
    Безопасная обёртка над Kafka.
    - При KAFKA_ENABLED=false вообще не импортирует kafka-python.
    - При ошибке импорта или недоступности брокера не валит сервис.
    """

    def __init__(self, client_id: str):
        self.client_id = client_id
        self._producer = None
        self._consumers = []
        self._lock = threading.Lock()

        if KAFKA_ENABLED:
            self._init_producer()
        else:
            logger.info(
                "Kafka disabled for client_id=%s; EventBus in no-op mode",
                self.client_id
            )

    def _init_producer(self):
        try:
            from kafka import KafkaProducer
            from kafka.errors import NoBrokersAvailable
        except Exception as e:
            logger.warning(
                "Kafka library unavailable for client_id=%s: %s",
                self.client_id, e
            )
            self._producer = None
            return

        for attempt in range(10):
            try:
                self._producer = KafkaProducer(
                    bootstrap_servers=KAFKA_BOOTSTRAP,
                    client_id=self.client_id,
                    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                    acks='all',
                    retries=3,
                    linger_ms=10,
                )
                logger.info(
                    "Kafka producer started: client_id=%s servers=%s",
                    self.client_id, KAFKA_BOOTSTRAP
                )
                return
            except NoBrokersAvailable:
                wait = min(2 ** attempt, 15)
                logger.warning(
                    "Kafka not available, retry in %ss (%s/10)",
                    wait, attempt + 1
                )
                time.sleep(wait)
            except Exception as e:
                logger.error(
                    "Kafka producer init error for client_id=%s: %s",
                    self.client_id, e
                )
                self._producer = None
                return

        logger.error("Kafka producer not started after retries")
        self._producer = None

    def publish(self, topic: str, payload: dict) -> bool:
        if not KAFKA_ENABLED:
            logger.debug("Kafka disabled, skip publish to %s", topic)
            return False

        if not self._producer:
            logger.debug("Kafka producer unavailable, skip publish to %s", topic)
            return False

        try:
            payload = dict(payload)
            payload.setdefault('source', self.client_id)
            payload.setdefault('ts', time.time())
            future = self._producer.send(topic, payload)
            future.get(timeout=3)
            return True
        except Exception as e:
            logger.error("Publish to %s failed: %s", topic, e)
            return False

    def subscribe(
        self,
        topic: str,
        handler: Callable[[dict], None],
        group_id: Optional[str] = None,
    ):
        """
        Запускает фоновый поток, читающий сообщения из Kafka.
        Если Kafka отключена/недоступна — просто пропускаем подписку.
        """
        if not KAFKA_ENABLED:
            logger.info("Kafka disabled, subscribe to %s skipped", topic)
            return

        if group_id is None:
            group_id = f"{self.client_id}-group"

        def _loop():
            try:
                from kafka import KafkaConsumer
                from kafka.errors import NoBrokersAvailable
            except Exception as e:
                logger.warning(
                    "Kafka consumer import failed for topic=%s: %s",
                    topic, e
                )
                return

            consumer = None

            for attempt in range(10):
                try:
                    consumer = KafkaConsumer(
                        topic,
                        bootstrap_servers=KAFKA_BOOTSTRAP,
                        group_id=group_id,
                        client_id=f"{self.client_id}-consumer",
                        value_deserializer=lambda b: json.loads(
                            b.decode('utf-8')
                        ),
                        auto_offset_reset='latest',
                        enable_auto_commit=True,
                    )
                    logger.info(
                        "Subscribed to %s (group=%s)",
                        topic, group_id
                    )
                    break
                except NoBrokersAvailable:
                    wait = min(2 ** attempt, 15)
                    logger.warning(
                        "Kafka not available for subscribe to %s, retry in %ss",
                        topic, wait
                    )
                    time.sleep(wait)
                except Exception as e:
                    logger.error(
                        "Kafka subscribe init error for topic=%s: %s",
                        topic, e
                    )
                    return

            if not consumer:
                logger.error("Subscribe to %s failed", topic)
                return

            for message in consumer:
                try:
                    handler(message.value)
                except Exception as e:
                    logger.exception(
                        "Handler error on topic %s: %s",
                        topic, e
                    )

        thread = threading.Thread(
            target=_loop,
            name=f"kafka-{topic}",
            daemon=True,
        )
        thread.start()
        self._consumers.append(thread)

    def close(self):
        with self._lock:
            if self._producer:
                try:
                    self._producer.flush(timeout=2)
                    self._producer.close(timeout=2)
                except Exception:
                    pass
                self._producer = None