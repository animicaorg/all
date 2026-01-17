from pathlib import Path

from core.snapshot.orchestrator import SnapshotConfig, SnapshotOrchestrator


class DummyBlockDB:
    def __init__(self, height: int) -> None:
        self._height = height

    def get_canonical_height(self) -> int:
        return self._height


class DummyStateDB:
    pass


def test_orchestrator_status_without_db_uri(tmp_path: Path) -> None:
    config = SnapshotConfig(auto_create=False, data_dir=tmp_path)
    orchestrator = SnapshotOrchestrator(
        block_db=DummyBlockDB(height=0),
        state_db=DummyStateDB(),
        chain_id=0,
        config=config,
    )

    status = orchestrator.get_status()
    assert status["config"]["auto_create"] is False
    assert status["status"]["head_height"] == 0
