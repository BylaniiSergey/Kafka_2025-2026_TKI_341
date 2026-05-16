"""
Тесты цепочки sensor_verification:
sensors_module -> exo.sensors.raw -> sensor_verification ->
exo.sensors.verified.

Проверяем поведение обработчика входящих сырых показаний.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def _load(tmp_path, monkeypatch, critical_snapshot):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('KAFKA_ENABLED', 'false')
    sys.modules.pop('sensor_verification_main', None)

    class FakeResp:
        def __init__(self, data, code=200):
            self._data = data
            self.status_code = code

        def json(self):
            return self._data

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return FakeResp(critical_snapshot)

        def post(self, url, json=None):
            return FakeResp({'ok': True})

    path = ROOT / 'sensor_verification' / 'main.py'
    spec = importlib.util.spec_from_file_location(
        'sensor_verification_main', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, 'httpx', type('h', (), {'Client': FakeClient}))
    return mod


class TestSensorVerificationKafka:
    def test_matching_readings_published_as_trusted(
        self, tmp_path, monkeypatch, fake_bus_factory
    ):
        critical = {'joint_angle': 45.1, 'joint_angular_velocity': 2.0}
        mod = _load(tmp_path, monkeypatch, critical)
        client = TestClient(mod.app)
        # активируем startup, иначе подписки не зарегистрируются
        with client:
            bus = fake_bus_factory()
            bus.deliver('exo.sensors.raw', {
                'joint_angle': 45.0,
                'joint_angular_velocity': 2.1,
                'torque': 35.0,
                'motor_temp': 40.0,
            })

        verified = [e for e in bus.published
                    if e['topic'] == 'exo.sensors.verified']
        assert len(verified) == 1
        payload = verified[0]['payload']
        assert payload['trusted'] is True
        assert payload['joint_angle'] == 45.0
        assert payload['torque'] == 35.0

    def test_large_deviation_marked_untrusted(
        self, tmp_path, monkeypatch, fake_bus_factory
    ):
        critical = {'joint_angle': 80.0, 'joint_angular_velocity': 2.0}
        mod = _load(tmp_path, monkeypatch, critical)
        client = TestClient(mod.app)
        with client:
            bus = fake_bus_factory()
            bus.deliver('exo.sensors.raw', {
                'joint_angle': 45.0,
                'joint_angular_velocity': 2.0,
            })

        verified = [e for e in bus.published
                    if e['topic'] == 'exo.sensors.verified']
        assert len(verified) == 1
        assert verified[0]['payload']['trusted'] is False

    def test_http_verify_endpoint_independent_from_kafka(
        self, tmp_path, monkeypatch, fake_bus_factory
    ):
        mod = _load(tmp_path, monkeypatch, {})
        client = TestClient(mod.app)
        with client:
            r = client.post('/verify', json={
                'metric': 'torque',
                'regular_value': 30.0,
                'critical_value': 30.5,
                'tolerance': 5.0,
            })
        body = r.json()
        assert body['passed'] is True
        assert body['deviation'] == 0.5

    def test_subscriber_registered_for_raw_topic(
        self, tmp_path, monkeypatch, fake_bus_factory
    ):
        mod = _load(tmp_path, monkeypatch, {})
        client = TestClient(mod.app)
        with client:
            bus = fake_bus_factory()
            assert 'exo.sensors.raw' in bus.subscriptions
