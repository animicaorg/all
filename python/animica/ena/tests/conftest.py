"""Shared fixtures for the ENA suite.

Most of these tests predate the requirement that a submitted shard must carry loadable
checkpoint weights. They exercise claim/aggregate/promote/serving logic and fabricate
shards with no adapter on disk, so they opt into the documented escape hatch rather than
each growing a fake safetensors file.

The requirement itself is covered deliberately by test_pool_checkpoint_required.py, which
CLEARS this flag — so the guard is still tested, and turning it on here cannot hide a
regression in it.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _allow_weightless_submit(monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_ALLOW_WEIGHTLESS_SUBMIT", "1")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "no_auto_adapter: do not attach a real LoRA adapter to submits — for tests that "
        "deliberately exercise the checkpoint-less path (hold instead of advance, etc.)")


@pytest.fixture(autouse=True)
def _supply_real_adapter(monkeypatch, tmp_path_factory, request):
    """Attach a REAL LoRA adapter to submits that don't specify one.

    These tests were written when a shard could be submitted with no weights, so they
    call submit_shard(...) without a checkpoint_path and then assert the round
    aggregates, promotes and serves. That is no longer possible for a weight-training
    pool (method defaults to lora): with nothing to merge, promotion correctly refuses
    with `no_finite_merged_adapter` and the served checkpoint is preserved.

    Rather than assert the broken behaviour, give those submits an actual tiny adapter so
    the REAL merge path runs — which is a strictly better thing for these tests to
    exercise than the plan-only fallback they used to hit. Tests that pass an explicit
    checkpoint_path (or that deliberately test the weightless guard) are untouched.
    """
    if request.node.get_closest_marker("no_auto_adapter"):
        return                             # the test IS the checkpoint-less path
    try:
        import torch
        from safetensors.torch import save_file
    except Exception:                      # no torch -> leave the suite as-is
        return

    d = tmp_path_factory.mktemp("ena-adapter")
    # Shapes/names mirror a real PEFT LoRA export closely enough for the weighted
    # average in _try_merge_adapters; identical tensors across shards make the merged
    # result exactly equal to the input, which keeps assertions stable.
    save_file({
        "base_model.model.layers.0.self_attn.q_proj.lora_A.weight": torch.ones(4, 8),
        "base_model.model.layers.0.self_attn.q_proj.lora_B.weight": torch.ones(8, 4),
    }, str(d / "adapter_model.safetensors"))
    (d / "adapter_config.json").write_text(
        '{"peft_type": "LORA", "r": 4, "lora_alpha": 8}', encoding="utf-8")

    from animica.ena.pool import PoolService
    original = PoolService.submit_shard

    def _submit(self, pool_id, shard_id, **kw):
        if not kw.get("checkpoint_path"):
            kw["checkpoint_path"] = str(d)
        return original(self, pool_id, shard_id, **kw)

    monkeypatch.setattr(PoolService, "submit_shard", _submit)
