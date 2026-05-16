"""
Юнит-тесты обёртки EventBus.

KafkaProducer и KafkaConsumer полностью замокированы, реальный
брокер не нужен.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def reload_kafka_bus(monkeypatch):
    import importlib
    import kafka_bus
    monkeypatch.setattr(kafka_bus, 'KAFKA_ENABLED', True)
    monkeypatch.setattr(kafka_bus, 'KAFKA_BOOTSTRAP', 'unit:9092')
    return importlib.reload(kafka_bus)


def test_publish_serializes_payload_and_sets_source(reload_kafka_bus):
    kb = reload_kafka_bus
    producer = MagicMock()
    future = MagicMock()
    future.get.return_value = None
    producer.send.return_value = future

    with patch.object(kb, 'KafkaProducer', return_value=producer):
        bus = kb.EventBus(client_id='unit_test')
        ok = bus.publish('exo.test', {'reason': 'check'})

    assert ok is True
    assert producer.send.call_count == 1
    args, _ = producer.send.call_args
    assert args[0] == 'exo.test'
    sent = args[1]
    assert sent['reason'] == 'check'
    assert sent['source'] == 'unit_test'
    assert isinstance(sent['ts'], float)


def test_publish_returns_false_on_broker_error(reload_kafka_bus):
    from kafka.errors import KafkaError
    kb = reload_kafka_bus
    producer = MagicMock()
    producer.send.side_effect = KafkaError('broker down')

    with patch.object(kb, 'KafkaProducer', return_value=producer):
        bus = kb.EventBus(client_id='unit_test')
        ok = bus.publish('exo.test', {'reason': 'x'})

    assert ok is False


def test_publish_no_op_when_producer_failed_to_start(reload_kafka_bus, monkeypatch):
    kb = reload_kafka_bus
    monkeypatch.setattr(kb, 'KAFKA_ENABLED', False)
    bus = kb.EventBus(client_id='unit_test')
    assert bus.publish('exo.test', {'reason': 'x'}) is False


def test_subscribe_starts_background_thread(reload_kafka_bus):
    """Подписка должна запустить демон-поток с консьюмером."""
    kb = reload_kafka_bus

    consumer_messages = []

    class FakeMessage:
        def __init__(self, value):
            self.value = value

    class FakeConsumer:
        def __init__(self, *args, **kwargs):
            self._messages = iter([FakeMessage({'reason': 'a'})])

        def __iter__(self):
            return self._messages

    received = []

    with patch.object(kb, 'KafkaProducer', return_value=MagicMock()), \
         patch.object(kb, 'KafkaConsumer', FakeConsumer):
        bus = kb.EventBus(client_id='unit_test')
        bus.subscribe('exo.test', handler=received.append, group_id='g')
        # Дождёмся обработки единственного сообщения
        import time
        for _ in range(20):
            if received:
                break
            time.sleep(0.05)

    assert received == [{'reason': 'a'}]


def test_handler_exception_does_not_kill_consumer(reload_kafka_bus):
    kb = reload_kafka_bus

    class FakeMessage:
        def __init__(self, value):
            self.value = value

    class FakeConsumer:
        def __init__(self, *args, **kwargs):
            self._messages = iter([
                FakeMessage({'n': 1}),
                FakeMessage({'n': 2}),
                FakeMessage({'n': 3}),
            ])

        def __iter__(self):
            return self._messages

    received = []

    def handler(payload):
        if payload['n'] == 2:
            raise RuntimeError('boom')
        received.append(payload['n'])

    with patch.object(kb, 'KafkaProducer', return_value=MagicMock()), \
         patch.object(kb, 'KafkaConsumer', FakeConsumer):
        bus = kb.EventBus(client_id='unit_test')
        bus.subscribe('exo.test', handler=handler, group_id='g')

        import time
        for _ in range(30):
            if len(received) >= 2:
                break
            time.sleep(0.05)

    assert received == [1, 3]
