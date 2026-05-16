"""
Тесты публикаторов в exo.emergency.

Проверяем, что модули контроля силы и проверки положения шлют
правильно оформленные события в шину при срабатывании условий.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, folder: str, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('KAFKA_ENABLED', 'false')
    sys.modules.pop(name, None)
    path = ROOT / folder / 'main.py'
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestArmForceLimits:
    """arm_force_limits_system публикует exo.emergency при превышении."""

    @pytest.fixture
    def env(self, tmp_path, monkeypatch, fake_bus_factory):
        # Подменяем httpx чтобы /snapshot отдавал нужные данные
        snapshot = {'readings': {
            'trusted': True,
            'elbow_left_deg': 90, 'elbow_right_deg': 90,
            'shoulder_left_deg': 45, 'shoulder_right_deg': 45,
            'pressure_left_n': 50, 'pressure_right_n': 50,
        }}

        class FakeResp:
            def __init__(self, data):
                self._data = data
                self.status_code = 200

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
                return FakeResp(snapshot)

            def post(self, url, json=None):
                return FakeResp({'ok': True})

        mod = _load('arm_force_limits_main',
                    'arm_force_limits_system', tmp_path, monkeypatch)
        monkeypatch.setattr(mod, 'httpx',
                            type('h', (), {'Client': FakeClient}))
        return mod, snapshot, fake_bus_factory()

    def test_normal_reading_does_not_publish(self, env):
        mod, snapshot, bus = env
        client = TestClient(mod.app)
        resp = client.post('/evaluate', json={
            'intent': 'lift_arm', 'arm': 'right',
            'strength': 20.0, 'speed_modifier': 0.5,
        })
        assert resp.status_code == 200
        assert resp.json()['ok'] is True
        assert bus.published == []

    def test_exceeded_pressure_publishes_emergency(self, env):
        mod, snapshot, bus = env
        snapshot['readings']['pressure_left_n'] = 250  # выше MAX (180)
        client = TestClient(mod.app)
        resp = client.post('/evaluate', json={
            'intent': 'grip', 'arm': 'left',
            'strength': 30.0, 'speed_modifier': 0.4,
        })
        body = resp.json()
        assert body['stop_system'] is True
        assert body['reason'] == 'pressure_emergency'
        assert len(bus.published) == 1
        ev = bus.published[0]
        assert ev['topic'] == 'exo.emergency'
        assert ev['payload']['reason'] == 'pressure_exceeded_critical'
        assert ev['payload']['source'] == 'arm_force_limits_system'

    def test_untrusted_critical_sensor_publishes(self, env):
        mod, snapshot, bus = env
        snapshot['readings']['trusted'] = False
        client = TestClient(mod.app)
        resp = client.post('/evaluate', json={
            'intent': 'idle', 'arm': 'none',
            'strength': 0.0, 'speed_modifier': 0.0,
        })
        assert resp.json()['stop_system'] is True
        assert any(
            e['payload']['reason'] == 'critical_arm_sensors_untrusted'
            for e in bus.published
        )


class TestLegForceLimits:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch, fake_bus_factory):
        snapshot = {'readings': {
            'trusted': True,
            'knee_left_deg': 90, 'knee_right_deg': 90,
            'hip_left_deg': 10, 'hip_right_deg': 10,
            'pressure_contact_left_n': 100,
            'pressure_contact_right_n': 100,
        }}

        class FakeResp:
            def __init__(self, data):
                self._data = data
                self.status_code = 200

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
                return FakeResp(snapshot)

            def post(self, url, json=None):
                return FakeResp({'ok': True})

        mod = _load('leg_force_limits_main',
                    'leg_force_limits_system', tmp_path, monkeypatch)
        monkeypatch.setattr(mod, 'httpx',
                            type('h', (), {'Client': FakeClient}))
        return mod, snapshot, fake_bus_factory()

    def test_knee_angle_exceeded(self, env):
        mod, snapshot, bus = env
        snapshot['readings']['knee_left_deg'] = 180  # > MAX_KNEE_DEG=170
        client = TestClient(mod.app)
        client.post('/evaluate', json={
            'intent': 'walk', 'leg': 'left',
            'strength': 30.0, 'speed_modifier': 0.4,
        })
        reasons = [e['payload']['reason'] for e in bus.published]
        assert 'leg_angle_limit_exceeded' in reasons


class TestPositionCheck:
    @pytest.fixture
    def env(self, tmp_path, monkeypatch, fake_bus_factory):
        mod = _load('position_check_main',
                    'position_check_module', tmp_path, monkeypatch)
        return mod, fake_bus_factory()

    def test_ins_inside_zone_no_emergency(self, env):
        mod, bus = env
        client = TestClient(mod.app)
        client.post('/ins_update', json={
            'x': 1.0, 'y': 1.0, 'in_zone': True,
        })
        assert bus.published == []

    def test_ins_out_of_zone_publishes(self, env):
        mod, bus = env
        client = TestClient(mod.app)
        client.post('/ins_update', json={
            'x': 9.0, 'y': 9.0, 'in_zone': False,
        })
        assert any(
            e['topic'] == 'exo.emergency'
            and e['payload']['source'] == 'position_check_module'
            for e in bus.published
        )

    def test_gnss_out_alone_does_not_publish(self, env):
        """Если GNSS ушёл за зону, но ИНС в порядке — аварии быть не должно."""
        mod, bus = env
        client = TestClient(mod.app)
        client.post('/ins_update', json={
            'x': 2.0, 'y': 2.0, 'in_zone': True,
        })
        bus.published.clear()
        client.post('/gnss_update', json={
            'x': 9.0, 'y': 9.0, 'in_zone': False,
        })
        assert bus.published == []
