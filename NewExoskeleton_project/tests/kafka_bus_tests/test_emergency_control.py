"""
Тесты emergency_control_module: проверяем что
- HTTP /emergency запускает открытие кабины и безопасную позу
- Сообщение из топика exo.emergency обрабатывается так же
- Источник сохраняется в БД с правильным transport
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def _load_emergency_control(tmp_path, monkeypatch):
    """Импортируем main.py emergency_control_module как отдельный модуль."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('KAFKA_ENABLED', 'false')
    sys.modules.pop('emergency_control_main', None)
    path = ROOT / 'emergency_control_module' / 'main.py'
    spec = importlib.util.spec_from_file_location('emergency_control_main', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def emergency_module(tmp_path, monkeypatch, fake_bus_factory):
    """
    Запускает emergency_control_module с подменённым EventBus и
    мок-httpx, чтобы вызовы к /open и /safe_pose не уходили в сеть.
    """
    calls = {'open': [], 'safe_pose': []}

    class FakeResponse:
        def __init__(self, status_code=200):
            self.status_code = status_code

        def json(self):
            return {'ok': True}

    class FakeHttpxClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None):
            if url.endswith('/open'):
                calls['open'].append(json)
            elif url.endswith('/safe_pose'):
                calls['safe_pose'].append(json)
            return FakeResponse(200)

    mod = _load_emergency_control(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, 'httpx', type('h', (), {'Client': FakeHttpxClient}))

    client = TestClient(mod.app)
    with client:
        yield mod, client, calls, fake_bus_factory()


class TestHttpPath:
    def test_emergency_endpoint_triggers_open_and_stop(self, emergency_module):
        mod, client, calls, bus = emergency_module
        resp = client.post('/emergency', json={
            'source': 'patient',
            'reason': 'panic_button',
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body['transport'] == 'http'
        assert body['source'] == 'patient'
        assert body['open_cabin']['success'] is True
        assert body['safe_pose']['success'] is True
        assert len(calls['open']) == 1
        assert len(calls['safe_pose']) == 1
        assert calls['open'][0]['reason'] == 'panic_button'

    def test_status_counters_increment(self, emergency_module):
        mod, client, calls, bus = emergency_module
        client.post('/emergency', json={'source': 'patient', 'reason': 'x'})
        client.post('/emergency', json={'source': 'monitoring', 'reason': 'y'})
        status = client.get('/status').json()
        assert status['total_events'] == 2
        assert status['http_events'] == 2
        assert status['kafka_events'] == 0
        assert status['emergency_active'] is True


class TestKafkaPath:
    def test_kafka_message_triggers_same_dispatch(self, emergency_module):
        mod, client, calls, bus = emergency_module
        bus.deliver(mod.TOPIC_EMERGENCY if hasattr(mod, 'TOPIC_EMERGENCY')
                    else 'exo.emergency',
                    {'source': 'critical_battery_monitor',
                     'reason': 'critical_battery'})
        assert len(calls['open']) == 1
        assert len(calls['safe_pose']) == 1
        status = client.get('/status').json()
        assert status['kafka_events'] == 1
        assert status['http_events'] == 0
        assert status['last_source'] == 'critical_battery_monitor'

    def test_kafka_handler_registered_on_correct_topic(self, emergency_module):
        mod, client, calls, bus = emergency_module
        assert 'exo.emergency' in bus.subscriptions


class TestReset:
    def test_reset_requires_authorized_source(self, emergency_module):
        mod, client, calls, bus = emergency_module
        client.post('/emergency', json={'source': 'patient', 'reason': 'x'})
        bad = client.post('/reset', params={'source': 'patient'})
        assert bad.status_code == 403
        good = client.post('/reset', params={'source': 'doctor_tablet'})
        assert good.status_code == 200
        assert good.json()['emergency_active'] is False


class TestHistory:
    def test_history_records_transport(self, emergency_module):
        mod, client, calls, bus = emergency_module
        client.post('/emergency', json={'source': 'patient', 'reason': 'h'})
        bus.deliver('exo.emergency', {'source': 'position_check_module',
                                       'reason': 'zone_breach'})
        hist = client.get('/history').json()
        assert len(hist) == 2
        transports = {h['transport'] for h in hist}
        assert transports == {'http', 'kafka'}
