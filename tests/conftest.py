from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from exoskeleton.api.app import create_app
from exoskeleton.control_system import ExoskeletonControlSystem


@pytest.fixture
def control() -> ExoskeletonControlSystem:
    return ExoskeletonControlSystem()


@pytest.fixture
def client(control: ExoskeletonControlSystem) -> TestClient:
    return TestClient(create_app(control))
