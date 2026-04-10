from __future__ import annotations

from pathlib import Path

from core.snapshot.inventory import latest_snapshot


def test_latest_snapshot_permission_denied_returns_none(
    tmp_path: Path, monkeypatch
) -> None:
    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    def _deny(_snapshots_dir=None):  # type: ignore[no-untyped-def]
        raise PermissionError(13, "Permission denied", "/data/snapshots")

    monkeypatch.setattr("core.snapshot.inventory.rebuild_inventory", _deny)
    assert latest_snapshot(chain_id=1, snapshots_dir=snapshots_dir) is None
