"""
Тесты critical_situation_recognition.

Модуль подписан на exo.sensors.verified, при выходе метрики за
пороги публикует событие в exo.emergency.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def _load(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('KAFKA_ENABLED', 'false')
    sys.modules.pop('critical_situation_main', None)
    path = ROOT / 'critical_situation_recognition' / 'main.py'
    spec = importlib.util.spec_from_file_location(
        'critical_situation_main', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch, fake_bus_factory):
    mod = _load(tmp_path, monkeypatch)
    client = TestClient(mod.app)
    with client:
        bus = fake_bus_factory()
        yield mod, client, bus


class TestSensorVerifiedHandler:
    def test_within_thresholds_no_emergency(self, env):
        mod, client, bus = env
        bus.deliver('exo.sensors.verified', {
            'trusted': True,
            'joint_angle': 50.0,
            'torque': 30.0,
            'motor_temp': 45.0,
        })
        assert [e for e in bus.published
                if e['topic'] == 'exo.emergency'] == []

    def test_overheat_publishes_emergency(self, env):
        mod, client, bus = env
        bus.deliver('exo.sensors.verified', {
            'trusted': True,
            'motor_temp': 95.0,
        })
        emergencies = [e for e in bus.published
                       if e['topic'] == 'exo.emergency']
        assert len(emergencies) == 1
        assert emergencies[0]['payload']['reason'] == 'critical_motor_temp'
        assert emergencies[0]['payload']['metric'] == 'motor_temp'

    def test_untrusted_data_skipped(self, env):
        mod, client, bus = env
        bus.deliver('exo.sensors.verified', {
            'trusted': False,
            'motor_temp': 999.0,
        })
        assert [e for e in bus.published
                if e['topic'] == 'exo.emergency'] == []


class TestHttpAnalyze:
    def test_analyze_critical_metric_publishes(self, env):
        mod, client, bus = env
        resp = client.post('/analyze', json={
            'metric': 'joint_angle',
            'value': 200.0,
            'source': 'sensors_module',
            'sensor_trusted': True,
        })
        body = resp.json()
        assert body['critical'] is True
        assert body['action'] == 'emergency_published'
        assert any(e['topic'] == 'exo.emergency' for e in bus.published)

    def test_analyze_untrusted_sensor_skipped(self, env):
        mod, client, bus = env
        resp = client.post('/analyze', json={
            'metric': 'joint_angle',
            'value': 200.0,
            'source': 'sensors_module',
            'sensor_trusted': False,
        })
        body = resp.json()
        assert body['critical'] is False
        assert body['action'] == 'ignore_untrusted'
        assert bus.published == []
