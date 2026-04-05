"""
Потребитель команд из Kafka и публикация телеметрии/событий.
"""
from __future__ import annotations
import argparse
import json
import sys
from typing import Any
from exoskeleton.control_system import ExoskeletonControlSystem
from exoskeleton.messaging.json_handler import CommandJsonHandler
from exoskeleton.messaging.kafka_topics import TOPIC_COMMANDS, TOPIC_EVENTS, TOPIC_TELEMETRY

def _run(bootstrap: str, group_id: str) -> None:
    try:
        from kafka import KafkaConsumer, KafkaProducer
    except ImportError:
        print(
            "Нужен пакет kafka-python: pip install \".[kafka]\"",
            file=sys.stderr,
        )
        sys.exit(1)

    ctrl = ExoskeletonControlSystem()
    handler = CommandJsonHandler(ctrl)

    consumer = KafkaConsumer(
        TOPIC_COMMANDS,
        bootstrap_servers=bootstrap,
        group_id=group_id,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    producer = KafkaProducer(
        bootstrap_servers=bootstrap,
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
    )

    print(f"Слушаю {TOPIC_COMMANDS} на {bootstrap!r}, группа {group_id!r}", flush=True)
    for msg in consumer:
        payload: dict[str, Any] = msg.value
        reply = handler.handle(payload)
        producer.send(TOPIC_TELEMETRY, reply)
        ev = reply.get("event")
        if ev:
            producer.send(TOPIC_EVENTS, ev)
        producer.flush()
        print(f"Обработано correlation_id={reply.get('correlation_id')}", flush=True)

def main() -> None:
    p = argparse.ArgumentParser(description="Kafka: команды → экзоскелет → телеметрия")
    p.add_argument(
        "--bootstrap",
        default="localhost:9092",
        help="bootstrap.servers",
    )
    p.add_argument(
        "--group",
        default="exoskeleton-control",
        help="consumer group id",
    )
    args = p.parse_args()
    _run(args.bootstrap, args.group)

if __name__ == "__main__":
    main()