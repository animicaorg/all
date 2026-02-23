from __future__ import annotations

import threading
from pathlib import Path

from animica_studio.services.dataset_bootstrap_service import BootstrapOptions, DatasetBootstrapService, _ShardWriter
from animica_studio.services.dataset_manager import DatasetManager


def test_bootstrap_estimate_contains_guardrails() -> None:
    svc = DatasetBootstrapService()
    est = svc.estimate("big")
    assert est["target_bytes"] >= 50 * 1024**3
    assert est["disk_needed_bytes"] > est["target_bytes"]
    assert est["download_bytes"] > 0
    assert len(est["eta_hours_range"]) == 2


def test_bootstrap_cancel_writes_resumable_state(tmp_path: Path) -> None:
    svc = DatasetBootstrapService()
    cancel = threading.Event()
    cancel.set()
    out = svc.bootstrap(
        options=BootstrapOptions(name="cancel-test", size_preset="starter", output_dir=tmp_path / "cancel-test", shard_size_bytes=1024 * 1024),
        progress_cb=lambda _p: None,
        cancel=cancel,
    )
    assert out["cancelled"] is True
    assert (tmp_path / "cancel-test" / "build_state.json").exists()


def test_shard_writer_rotates_and_hashes(tmp_path: Path) -> None:
    writer = _ShardWriter(tmp_path / "shards", shard_size_bytes=120)
    writer.write({"text": "a" * 80})
    writer.write({"text": "b" * 80})
    shards = writer.close()
    assert len(shards) >= 2
    assert all(s["size_bytes"] > 0 and s["sha256"] for s in shards)


def test_dataset_manager_exposes_bootstrap_estimate() -> None:
    manager = DatasetManager()
    est = manager.estimate_bootstrap("starter")
    assert est["target_bytes"] >= 5 * 1024**3
