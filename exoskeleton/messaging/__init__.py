"""Обмен сообщениями: JSON-команды и (опционально) Kafka."""
from exoskeleton.messaging.json_handler import CommandJsonHandler
from exoskeleton.messaging.kafka_topics import TOPIC_COMMANDS, TOPIC_EVENTS, TOPIC_TELEMETRY

__all__ = [
    "CommandJsonHandler",
    "TOPIC_COMMANDS",
    "TOPIC_EVENTS",
    "TOPIC_TELEMETRY",
]