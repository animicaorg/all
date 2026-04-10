import asyncio
import logging
from pathlib import Path

from core.snapshot.orchestrator import SnapshotConfig, SnapshotOrchestrator


class DummyBlockDB:
    def __init__(self, height) -> None:
        self._height = height

    def get_canonical_height(self):
        return self._height


class DummyStateDB:
    pass


def test_orchestrator_status_without_db_uri(tmp_path: Path) -> None:
    config = SnapshotConfig(auto_create=False, data_dir=tmp_path)
    orchestrator = SnapshotOrchestrator(
        block_db=DummyBlockDB(height=0),
        state_db=DummyStateDB(),
        chain_id=1,
        config=config,
    )

    status = orchestrator.get_status()
    assert status["config"]["auto_create"] is False
    assert status["status"]["head_height"] == 0


def test_orchestrator_status_tolerates_missing_canonical_height(
    tmp_path: Path, caplog
) -> None:
    config = SnapshotConfig(auto_create=False, data_dir=tmp_path)
    orchestrator = SnapshotOrchestrator(
        block_db=DummyBlockDB(height=None),
        state_db=DummyStateDB(),
        chain_id=1,
        config=config,
    )

    with caplog.at_level(logging.WARNING, logger="animica.snapshot.orchestrator"):
        status = orchestrator.get_status()

    assert status["status"]["head_height"] is None
    assert "Failed to read canonical height" not in caplog.text


def test_orchestrator_health_check_tolerates_missing_canonical_height(
    tmp_path: Path,
) -> None:
    config = SnapshotConfig(auto_create=False, data_dir=tmp_path)
    orchestrator = SnapshotOrchestrator(
        block_db=DummyBlockDB(height=None),
        state_db=DummyStateDB(),
        chain_id=1,
        config=config,
    )

    healthy = asyncio.run(orchestrator.perform_health_check())

    assert healthy is True
    assert not orchestrator.status.warnings
