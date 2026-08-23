"""A shard with no loadable checkpoint must FAIL, not earn weight.

This is the defect that stalled the live pool. From round 49 to 83 trainers submitted
with checkpoint_path=None: the coordinator minted an ANCHOR hash (indistinguishable from
a weights digest), marked the shard `submitted`, and paid weight — which is gpu_hours,
i.e. wall clock, so a shard that merely sat open earned MORE. Every merge-plan for those
rounds recorded `checkpoints: {shard: null}`, nothing was mergeable, and the promoted head
stayed frozen at round 48 for six weeks while 259 unpayable contributions accumulated.

These tests clear ANIMICA_ENA_ALLOW_WEIGHTLESS_SUBMIT (the suite's conftest sets it) so
the guard itself is under test.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from animica.ena.pool import _checkpoint_digest, merge_checkpoints


@pytest.fixture(autouse=True)
def _require_weights(monkeypatch):
    monkeypatch.delenv("ANIMICA_ENA_ALLOW_WEIGHTLESS_SUBMIT", raising=False)


SHARD = {"shard_id": "pool-r99-s0", "sha256": "a" * 64}


def test_no_checkpoint_reports_anchor_not_weights():
    digest, has_weights = _checkpoint_digest(None, SHARD)
    assert has_weights is False, "an absent checkpoint must not look like real weights"
    assert digest, "an anchor digest is still returned for identity purposes"


def test_directory_without_weight_files_is_not_weights(tmp_path):
    d = tmp_path / "ckpt"
    d.mkdir()
    (d / "merge-plan.json").write_text("{}", encoding="utf-8")
    (d / "adapter_config.json").write_text("{}", encoding="utf-8")
    _, has_weights = _checkpoint_digest(str(d), SHARD)
    assert has_weights is False, "config files alone are not a checkpoint"


def test_real_adapter_hashes_the_actual_bytes(tmp_path):
    d = tmp_path / "ckpt"
    d.mkdir()
    (d / "adapter_model.safetensors").write_bytes(b"weights-bytes")
    digest, has_weights = _checkpoint_digest(str(d), SHARD)
    assert has_weights is True
    assert digest == hashlib.sha3_256(b"weights-bytes").hexdigest(), (
        "the digest must cover the weights, so a changed adapter changes the hash")


def test_anchor_and_real_digests_differ(tmp_path):
    d = tmp_path / "ckpt"
    d.mkdir()
    (d / "adapter_model.safetensors").write_bytes(b"weights-bytes")
    anchor, _ = _checkpoint_digest(None, SHARD)
    real, _ = _checkpoint_digest(str(d), SHARD)
    assert anchor != real


def test_merge_records_why_it_produced_nothing(tmp_path):
    """The stalled rounds wrote plans full of nulls with no stated reason."""
    res = merge_checkpoints(
        [{"shard_id": "s0", "checkpoint_path": None},
         {"shard_id": "s1", "checkpoint_path": None}],
        tmp_path / "out", {"s0": 0.5, "s1": 0.5})
    assert res["merged"] is False
    assert res["reason"] == "no_checkpoint_weights"
    assert res["shards_without_checkpoint"] == ["s0", "s1"]
    plan = json.loads((tmp_path / "out" / "merge-plan.json").read_text())
    assert plan["reason"] == "no_checkpoint_weights", (
        "the artifact itself must say why, not just the return value")
    assert plan["merged"] is False


def test_merge_distinguishes_partial_from_total_absence(tmp_path):
    res = merge_checkpoints(
        [{"shard_id": "s0", "checkpoint_path": "/nonexistent/a"},
         {"shard_id": "s1", "checkpoint_path": None}],
        tmp_path / "out2", {"s0": 0.5, "s1": 0.5})
    assert res["reason"] == "partial_checkpoints"
    assert res["shards_without_checkpoint"] == ["s1"]
