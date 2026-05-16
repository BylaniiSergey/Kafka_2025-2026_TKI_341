"""
Общая инфраструктура для тестов Kafka-цепочек.

FakeBus подменяет настоящий EventBus, чтобы тесты не требовали
запущенного брокера. Все опубликованные сообщения складываются
в список published, а зарегистрированные подписчики хранятся в
subscriptions — это позволяет вручную имитировать приход события.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, List, Dict, Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeBus:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.published: List[Dict[str, Any]] = []
        self.subscriptions: Dict[str, Callable[[dict], None]] = {}
        self.closed = False

    def publish(self, topic: str, payload: dict) -> bool:
        record = {'topic': topic, 'payload': dict(payload)}
        record['payload'].setdefault('source', self.client_id)
        self.published.append(record)
        return True

    def subscribe(self, topic, handler, group_id=None):
        self.subscriptions[topic] = handler

    def deliver(self, topic: str, payload: dict):
        handler = self.subscriptions.get(topic)
        assert handler is not None, f"No subscriber for {topic}"
        handler(payload)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_bus_factory(monkeypatch):
    """
    Подменяет kafka_bus.EventBus на FakeBus для всего теста.
    Возвращает функцию-фабрику, которая отдаёт последний созданный
    инстанс — это удобно, потому что модули создают bus при импорте.
    """
    instances: List[FakeBus] = []

    def factory(client_id: str):
        bus = FakeBus(client_id)
        instances.append(bus)
        return bus

    import kafka_bus
    monkeypatch.setattr(kafka_bus, 'EventBus', factory)
    monkeypatch.setattr(kafka_bus, 'KAFKA_ENABLED', False)

    def get_last() -> FakeBus:
        assert instances, "Bus was not created"
        return instances[-1]

    get_last.instances = instances
    return get_last


@pytest.fixture
def reload_module(monkeypatch):
    """
    Перезагружает модуль с уже подменёнными зависимостями.
    Используется, чтобы глобальный bus в модуле стал FakeBus.
    """
    import importlib

    def _reload(module_name: str):
        if module_name in sys.modules:
            return importlib.reload(sys.modules[module_name])
        return importlib.import_module(module_name)

    return _reload
