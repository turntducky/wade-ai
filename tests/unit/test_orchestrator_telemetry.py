import pytest
import asyncio

from app.core.telemetry import TelemetryStore
from app.core.orchestrator import Orchestrator
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.fixture
def telemetry(tmp_path):
    return TelemetryStore(tmp_path / "tel.db")

@pytest.fixture
def orchestrator(telemetry):
    orc = Orchestrator()
    orc.set_telemetry(telemetry)
    return orc

def test_set_telemetry_stores_instance(orchestrator, telemetry):
    assert orchestrator._telemetry is telemetry

def test_orchestrator_has_set_telemetry_method():
    orc = Orchestrator()
    assert hasattr(orc, "set_telemetry")
    assert callable(orc.set_telemetry)

def test_orchestrator_without_telemetry_has_none():
    orc = Orchestrator()
    assert orc._telemetry is None