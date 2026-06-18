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
    assert len({r["shard_id"] for r in results}) == 2  # two distinct round-1 shards

    # The COORDINATOR recorded the contributions + uploaded checkpoints.
    contribs = coord_ena.store.list_contributions(pid)
    assert len([c for c in contribs if c["role"] == "trainer"]) == 2
    uploads = Path(coord_ena.cfg.artifacts_dir()) / "pools" / pid / "uploads"
    assert uploads.is_dir() and any(uploads.iterdir()), "checkpoints were not uploaded"

    # The final submit completed the round → the coordinator AUTO-promoted a
    # checkpoint (no manual aggregate call). The worker then fetches it.
    assert results[-1].get("auto_aggregated") is True, results[-1]
    served = coord_ena.pool.get_served_checkpoint(pid)  # raises if not promoted
    assert served["checkpoint_hash"]
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


def test_serve_before_first_promotion_signals_not_ready(coordinator, tmp_path, monkeypatch):
    """A fresh pool has no promoted checkpoint; the remote serve path must raise a
    'no promoted checkpoint' RemoteError (which the CLI serve-loop waits on),
    NOT some opaque crash."""
    coord_ena, base = coordinator
    dataset = _write_dataset(tmp_path / "d.jsonl", n=8)
    pool = coord_ena.pool.create(base_model="tiny", dataset=dataset,
                                 method="lora", name="fresh", num_shards=1)
    pid = pool["pool_id"]
    worker = _make_ena(tmp_path / "srvworker", monkeypatch)
    with pytest.raises(RemoteError) as ei:
        worker.serve_model(pid, worker_id="srv", host="127.0.0.1", port=0,
                           endpoint=base)
    assert "no promoted checkpoint" in str(ei.value).lower()


def test_auto_aggregate_promotes_when_round_completes(coordinator, tmp_path, monkeypatch):
    """Submitting the last shard of a round auto-promotes a checkpoint with no
    explicit aggregate() call (eval-gate-free pool)."""
    coord_ena, _ = coordinator
    dataset = _write_dataset(tmp_path / "d.jsonl", n=12)
    pool = coord_ena.pool.create(base_model="tiny", dataset=dataset,
                                 method="lora", name="auto", num_shards=2)
    pid = pool["pool_id"]
    # Two shards, submitted with no checkpoint (merge-plan fallback) — promotion
    # should still fire on the final submit.
    seen_promote = []
    for _ in range(2):
        claimed = coord_ena.pool.claim_shard(pid, "w-local")
        assert claimed is not None
        res = coord_ena.pool.submit_shard(
            pid, claimed["shard_id"], worker_id="w-local",
            metrics={"samples": 5}, miner_address="anim1w")
        seen_promote.append(res.get("auto_aggregated"))
    assert seen_promote == [None, True], seen_promote  # only the final submit promotes
    served = coord_ena.pool.get_served_checkpoint(pid)
    assert served["round"] == 1 and served["checkpoint_hash"]
    assert coord_ena.pool.get(pid)["round"] == 2  # round advanced


def test_gated_pool_rejects_weak_candidate_but_advances(coordinator, tmp_path,
                                                        monkeypatch):
    """An eval-gated pool auto-evaluates its candidate on round completion: a
    weak candidate is NOT served (gate protects the model) but the round still
    advances so the curriculum can retarget and the flywheel keeps turning."""
    coord_ena, _ = coordinator
    dataset = _write_dataset(tmp_path / "d.jsonl", n=8)
    pool = coord_ena.pool.create(base_model="tiny", dataset=dataset, method="lora",
                                 name="gated", num_shards=1,
                                 eval_gate={"min_score": 0.5, "metric": "acc"})
    pid = pool["pool_id"]
    # candidate scores below the 0.5 gate
    monkeypatch.setattr(coord_ena.curriculum, "evaluate_candidate",
                        lambda p, c: 0.1)
    claimed = coord_ena.pool.claim_shard(pid, "w")
    res = coord_ena.pool.submit_shard(pid, claimed["shard_id"], worker_id="w",
                                      metrics={"samples": 5})
    assert not res.get("auto_aggregated")  # gate blocked promotion
    p2 = coord_ena.store.get_pool(pid)
    assert p2["served_checkpoint"] is None  # regressed candidate not served
    assert p2["round"] == 2                 # but the round advanced
    assert (p2.get("metadata") or {}).get("rejected_rounds")


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
