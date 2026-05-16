"""
Тесты маршрутизации команд в task_orchestrator.

Обычные команды должны попадать в exo.commands, аварийные - в
exo.emergency. Untrusted-команды отклоняются HTTP 403.
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
    sys.modules.pop('task_orchestrator_main', None)
    path = ROOT / 'task_orchestrator' / 'main.py'
    spec = importlib.util.spec_from_file_location(
        'task_orchestrator_main', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def orchestrator(tmp_path, monkeypatch, fake_bus_factory):
    mod = _load(tmp_path, monkeypatch)
    return mod, TestClient(mod.app), fake_bus_factory()


class TestRouting:
    def test_regular_command_goes_to_commands_topic(self, orchestrator):
        mod, client, bus = orchestrator
        resp = client.post('/dispatch', json={
            'source': 'doctor_tablet',
            'target': 'control_system',
            'command': 'start_arms',
            'payload': {'signals': {'flex': 0.5}},
            'verification_token': 'SECURE_abc123',
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body['topic'] == 'exo.commands'

        assert len(bus.published) == 1
        ev = bus.published[0]
        assert ev['topic'] == 'exo.commands'
        assert ev['payload']['command'] == 'start_arms'
        assert ev['payload']['target'] == 'control_system'

    def test_emergency_command_goes_to_emergency_topic(self, orchestrator):
        mod, client, bus = orchestrator
        resp = client.post('/dispatch', json={
            'source': 'doctor_tablet',
            'target': 'emergency_control_module',
            'command': 'emergency_stop',
            'payload': {},
            'verification_token': 'SECURE_xyz',
        })
        assert resp.status_code == 200
        assert resp.json()['topic'] == 'exo.emergency'
        assert bus.published[0]['topic'] == 'exo.emergency'
        assert bus.published[0]['payload']['reason'] == 'emergency_stop'

    def test_untrusted_token_rejected(self, orchestrator):
        mod, client, bus = orchestrator
        resp = client.post('/dispatch', json={
            'source': 'attacker',
            'target': 'control_system',
            'command': 'start_arms',
            'payload': {},
            'verification_token': '',
        })
        assert resp.status_code == 403
        assert bus.published == []

    def test_dispatch_logged_to_db(self, orchestrator):
        mod, client, bus = orchestrator
        client.post('/dispatch', json={
            'source': 'doctor_tablet', 'target': 'control_system',
            'command': 'start_legs',
            'payload': {}, 'verification_token': 'SECURE_1',
        })
        session = mod.SessionLocal()
        try:
            rows = session.query(mod.TaskLogDB).all()
            assert len(rows) == 1
            assert rows[0].command == 'start_legs'
            assert rows[0].trusted is True
            assert rows[0].emergency is False
            assert rows[0].status == 'routed'
        finally:
            session.close()
