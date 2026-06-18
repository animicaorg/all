"""Distributed-training tests: a worker on its *own* store trains a pool that
lives on a remote coordinator, entirely over HTTP.

Boots the stdlib ENA coordinator on an ephemeral port, then drives a second ENA
(separate ENA_HOME / empty local store) through the remote claim → download
shard → train → upload checkpoint → submit → aggregate → fetch-promoted flow.
Training itself is stubbed (no GPU/torch) so the *distributed control flow* is
what's under test.
"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from animica.ena import ENA
from animica.ena import training as _training
from animica.ena.config import load_config
from animica.ena.remote import RemotePool, RemoteError
from animica.ena.service import _make_handler


def _write_dataset(path: Path, n: int = 24) -> str:
    rows = [{"prompt": f"Q{i}", "response": f"A{i}"} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


def _make_ena(home: Path, monkeypatch) -> ENA:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(home))
    return ENA(cfg=load_config())


@pytest.fixture()
def coordinator(tmp_path, monkeypatch):
    """A coordinator ENA + HTTP service. Yields (ena, base_url)."""
    ena = _make_ena(tmp_path / "coord", monkeypatch)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(ena))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield ena, f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _stub_training_run(seen: dict):
    """A drop-in for training.run that asserts the shard data was downloaded
    locally, then writes a fake LoRA adapter and reports success."""
    def _run(cfg, store, *, manifest_path: str, backend=None):
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        ds = manifest.get("train_dataset")
        assert ds and Path(ds).is_file(), "shard data was not downloaded locally"
        seen["rows"] = len(Path(ds).read_text(encoding="utf-8").splitlines())
        out = Path(manifest["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        # Write a *valid* tiny adapter so the coordinator's real torch+safetensors
        # merge path (now a base dep) accepts it.
        import torch
        from safetensors.torch import save_file
        save_file({"lora_A": torch.zeros(2, 2)},
                  str(out / "adapter_model.safetensors"))
        return {"run_id": "run-stub-" + manifest["metadata"]["shard_id"],
                "status": "completed", "output_dir": str(out),
                "metrics": {"samples": seen["rows"], "train_loss": 0.42}}
    return _run


def test_remote_distributed_training_end_to_end(coordinator, tmp_path, monkeypatch):
    coord_ena, base = coordinator

    # Pool + dataset live ONLY on the coordinator.
    dataset = _write_dataset(tmp_path / "data.jsonl", n=24)
    pool = coord_ena.pool.create(base_model="tiny", dataset=dataset,
                                 method="lora", name="dist-demo", num_shards=2)
    pid = pool["pool_id"]

    # The worker has its own empty store — proves the pool is resolved remotely.
    worker = _make_ena(tmp_path / "worker", monkeypatch)
    assert worker.pool.get_served_checkpoint  # sanity
    assert worker.store.get_pool(pid) is None, "worker store should not know the pool"

    seen: dict = {}
    monkeypatch.setattr(_training, "run", _stub_training_run(seen))

    # Train both shards remotely.
    results = []
    for _ in range(2):
        res = worker.train_shard_once(pid, worker_id="w-remote",
                                      address="anim1worker", endpoint=base)
        assert res is not None and res["status"] == "submitted", res
        assert len(res["receipt_hash"]) == 64
        results.append(res)
    assert seen["rows"] > 0  # the stub actually saw downloaded rows
    assert len({r["shard_id"] for r in results}) == 2  # two distinct shards

    # No more shards this round → None (loop would idle, not crash).
    assert worker.train_shard_once(pid, worker_id="w-remote", endpoint=base) is None

    # The COORDINATOR recorded the contributions + uploaded checkpoints.
    contribs = coord_ena.store.list_contributions(pid)
    assert len([c for c in contribs if c["role"] == "trainer"]) == 2
    uploads = Path(coord_ena.cfg.artifacts_dir()) / "pools" / pid / "uploads"
    assert uploads.is_dir() and any(uploads.iterdir()), "checkpoints were not uploaded"

    # Aggregate + promote on the coordinator, then the worker fetches the
    # promoted checkpoint over HTTP and extracts it locally.
    agg = coord_ena.pool.aggregate(pid)
    assert agg["promoted"] is True
    rc = RemotePool(base)
    promoted = rc.download_promoted(pid)
    assert promoted["checkpoint_hash"] and promoted["content_b64"]

    from animica.ena.remote import extract_tar_b64
    dest = tmp_path / "fetched"
    extract_tar_b64(promoted["content_b64"], dest)
    assert any(dest.rglob("*")), "promoted checkpoint extracted no files"


def test_remote_pool_not_found_raises(coordinator):
    _, base = coordinator
    rc = RemotePool(base)
    with pytest.raises(RemoteError) as ei:
        rc.claim("enapool-does-not-exist", "w1")
    assert "not found" in str(ei.value).lower()


def test_shard_data_route_returns_rows(coordinator, tmp_path):
    coord_ena, base = coordinator
    dataset = _write_dataset(tmp_path / "d.jsonl", n=12)
    pool = coord_ena.pool.create(base_model="tiny", dataset=dataset,
                                 method="lora", name="d", num_shards=1)
    pid = pool["pool_id"]
    rc = RemotePool(base)
    claimed = rc.claim(pid, "w1")
    assert claimed is not None
    data = rc.download_shard(claimed["shard_id"])
    assert data["content_b64"] and data["row_count"] >= 1
