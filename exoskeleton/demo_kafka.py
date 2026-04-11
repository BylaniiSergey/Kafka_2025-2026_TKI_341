"""
Сценарий с топиками Kafka и JSON (без изменения логики модулей).

Без установленного kafka-python и без брокера: показывается тот же поток сообщений
«как будто» они прошли через топики (мок).

Реальный брокер: pip install ".[kafka]" и python -m exoskeleton.messaging.kafka_runner
"""

from __future__ import annotations

import json

from exoskeleton.control_system import ExoskeletonControlSystem
from exoskeleton.messaging import TOPIC_COMMANDS, TOPIC_EVENTS, TOPIC_TELEMETRY
from exoskeleton.messaging.json_handler import CommandJsonHandler


def _print(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def _as_json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def main() -> None:
    ctrl = ExoskeletonControlSystem()
    handler = CommandJsonHandler(ctrl)

    samples: list[dict[str, object]] = [
        {"action": "initialize", "correlation_id": "c1"},
        {
            "action": "start_session",
            "source": "patient",
            "correlation_id": "c2",
        },
        {
            "action": "tactile_contact",
            "intensity": 0.35,
            "monitoring_ok": True,
            "correlation_id": "c3",
        },
        {
            "action": "update_climate",
            "body_temp_c": 37.4,
            "air_temp_c": 29.0,
            "correlation_id": "c4",
        },
        {
            "action": "emergency_stop",
            "source": "patient",
            "correlation_id": "c5",
        },
        {
            "action": "open_carriage",
            "source": "patient",
            "emergency": True,
            "correlation_id": "c6",
        },
        {
            "action": "reset_emergency",
            "source": "doctor_tablet",
            "correlation_id": "c7",
        },
        {"action": "telemetry", "correlation_id": "c8"},
    ]

    _print("Прототип Kafka: топики и JSON (мок, брокер не нужен)")
    print(f"Топик команд:     {TOPIC_COMMANDS}")
    print(f"Топик телеметрии: {TOPIC_TELEMETRY}")
    print(f"Топик событий:    {TOPIC_EVENTS} (в ответе на emergency — поле event)")

    for cmd in samples:
        line = json.dumps(cmd, ensure_ascii=False)
        print()
        print(f"→ [{TOPIC_COMMANDS}] {line}")
        reply = handler.handle(cmd)
        print(f"← [{TOPIC_TELEMETRY}] {_as_json(reply)}")
        ev = reply.get("event")
        if ev:
            print(f"← [{TOPIC_EVENTS}] {_as_json(ev)}")

    print()
    print("Готово. Для живого Kafka: pip install \".[kafka]\"")
    print("Затем: python -m exoskeleton.messaging.kafka_runner --bootstrap localhost:9092")
    print()


if __name__ == "__main__":
    main()
