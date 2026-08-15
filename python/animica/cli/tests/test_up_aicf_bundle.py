"""`animica up` installs every model bundle the machine can serve.

The live network had ZERO serving workers because every machine registered with
``tiers: []``: AICFWorker drops any tier without a bundle in
``$ANIMICA_DATA_DIR/models/<tier>/*/{manifest,inference}.json``, and `up` only
ever PRINTED "run aicf-worker pull" instead of doing it.

These tests pin two things that pull in opposite directions, which is why they
are worth having:

  * a rig that CAN serve flagship must end up serving flagship, not just tiny;
  * a rig that CANNOT must never download weights it can never load. ``large``
    is DeepSeek-Coder-V2-Instruct (236B params, 80 GB VRAM) — pulling it onto a
    laptop is hundreds of gigabytes of dead files.

Nothing here touches the network or the disk: agent_runtime is stubbed.
"""
from __future__ import annotations

import sys
import time
import types

import pytest

from animica.cli import up as upmod


CATALOG = {
    "tiers": [
        {"id": "tiny", "base_model": "Qwen/Qwen2.5-Coder-1.5B-Instruct", "min_vram_gb": 0},
        {"id": "small", "base_model": "Qwen/Qwen2.5-Coder-7B-Instruct", "min_vram_gb": 16},
        {"id": "flagship", "base_model": "deepseek-ai/DeepSeek-Coder-V2-Lite-Instruct", "min_vram_gb": 24},
        {"id": "large", "base_model": "deepseek-ai/DeepSeek-Coder-V2-Instruct", "min_vram_gb": 80},
    ],
}

_ENV_KEYS = (
    "ANIMICA_AICF_NO_AUTOPULL",
    "ANIMICA_AICF_PIPELINE_MODEL_ID",
    "ANIMICA_AICF_ADVERTISE_WITHOUT_BUNDLE",
    "ANIMICA_AICF_AUTOPULL_TIERS",
    "ANIMICA_AICF_AUTOPULL_MIN_FREE_GB",
)


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, s) -> None:  # noqa: ANN001 - rich-compatible shim
        self.lines.append(str(s))


@pytest.fixture
def bundle_env(monkeypatch):
    """Stub agent_runtime + the disk probe; expose knobs per scenario."""
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)

    state = {
        "installed": set(),       # tiers that already have a bundle
        "eligible": ["tiny"],     # what the hardware probe reports
        "fail": set(),            # tiers whose download raises
        "free_gb": 500.0,
        "calls": [],
    }

    worker = types.ModuleType("agent_runtime.aicf_worker")
    worker._has_servable_bundle = lambda tier: tier in state["installed"]

    def _bootstrap(tier, **_kw):
        state["calls"].append(tier)
        if tier in state["fail"]:
            raise RuntimeError("network down")
        return f"/root/.animica/models/{tier}/hf-x"

    worker.bootstrap_bundle_from_hf = _bootstrap

    cfgmod = types.ModuleType("agent_runtime.config")
    cfgmod.load_config = lambda: types.SimpleNamespace(model_catalog=CATALOG)

    hw = types.ModuleType("agent_runtime.hardware")
    hw.detect_hardware = lambda: object()
    hw.eligible_tiers = lambda _p, _c: list(state["eligible"])

    monkeypatch.setitem(sys.modules, "agent_runtime.aicf_worker", worker)
    monkeypatch.setitem(sys.modules, "agent_runtime.config", cfgmod)
    monkeypatch.setitem(sys.modules, "agent_runtime.hardware", hw)

    import shutil
    monkeypatch.setattr(
        shutil, "disk_usage",
        lambda _p: types.SimpleNamespace(
            total=0, used=0, free=int(state["free_gb"] * 1024 ** 3)),
    )
    return state


def _run(state, expect_calls=True):
    console = _Console()
    upmod._ensure_aicf_bundle(console)
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if any("finished" in ln or "stopping before" in ln for ln in console.lines):
            break
        if not expect_calls and console.lines:
            break
        time.sleep(0.01)
    time.sleep(0.05)
    return console.lines


def test_pulls_every_tier_the_hardware_can_serve(bundle_env):
    """A 24 GB rig serves tiny+small+flagship, so it installs all three."""
    bundle_env["eligible"] = ["tiny", "small", "flagship"]
    lines = _run(bundle_env)
    assert bundle_env["calls"] == ["tiny", "small", "flagship"], (
        "must install every eligible tier, smallest first"
    )
    assert any("tier(s) tiny, small, flagship" in ln for ln in lines)


def test_never_pulls_a_tier_the_hardware_cannot_load(bundle_env):
    """A CPU box is eligible for tiny only — `large` is 236B params."""
    bundle_env["eligible"] = ["tiny"]
    _run(bundle_env)
    assert bundle_env["calls"] == ["tiny"]
    assert "large" not in bundle_env["calls"]
    assert "flagship" not in bundle_env["calls"]


def test_smallest_first_so_the_node_becomes_servable_early(bundle_env):
    bundle_env["eligible"] = ["flagship", "tiny", "small"]  # catalog order wins
    _run(bundle_env)
    assert bundle_env["calls"] == ["tiny", "small", "flagship"]


def test_only_missing_tiers_are_fetched(bundle_env):
    """An interrupted run resumes instead of re-downloading."""
    bundle_env["eligible"] = ["tiny", "small", "flagship"]
    bundle_env["installed"] = {"tiny", "small"}
    lines = _run(bundle_env)
    assert bundle_env["calls"] == ["flagship"]
    assert any("already installed: tiny, small" in ln for ln in lines)


def test_fully_installed_is_a_silent_no_op(bundle_env):
    bundle_env["eligible"] = ["tiny", "small"]
    bundle_env["installed"] = {"tiny", "small"}
    lines = _run(bundle_env, expect_calls=False)
    assert bundle_env["calls"] == []
    assert lines == [], "must not nag on every subsequent `up`"


def test_stops_before_filling_the_disk(bundle_env):
    bundle_env["eligible"] = ["tiny", "small", "flagship"]
    bundle_env["free_gb"] = 5.0        # below the 20 GB default floor
    lines = _run(bundle_env, expect_calls=False)
    assert bundle_env["calls"] == [], "a full disk on a mining node is not an acceptable cost"
    assert any("only 5.0 GB free" in ln for ln in lines)
    assert any("Remaining tiers: tiny, small, flagship" in ln for ln in lines)


def test_one_failed_tier_does_not_abort_the_rest(bundle_env):
    bundle_env["eligible"] = ["tiny", "small", "flagship"]
    bundle_env["fail"] = {"small"}
    lines = _run(bundle_env)
    assert bundle_env["calls"] == ["tiny", "small", "flagship"]
    assert any("'small' bundle fetch failed" in ln for ln in lines)
    assert any("finished" in ln for ln in lines)


def test_explicit_tier_list_overrides_the_hardware_probe(bundle_env):
    bundle_env["eligible"] = ["tiny"]
    import os
    os.environ["ANIMICA_AICF_AUTOPULL_TIERS"] = "flagship,large"
    try:
        _run(bundle_env)
    finally:
        os.environ.pop("ANIMICA_AICF_AUTOPULL_TIERS", None)
    assert bundle_env["calls"] == ["flagship", "large"], (
        "an operator who pins the list knows better than the probe"
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("ANIMICA_AICF_NO_AUTOPULL", "1"),
        # These modes serve WITHOUT a local bundle, so a download is pure waste.
        ("ANIMICA_AICF_PIPELINE_MODEL_ID", "some-model"),
        ("ANIMICA_AICF_ADVERTISE_WITHOUT_BUNDLE", "1"),
    ],
)
def test_opt_outs_skip_everything(bundle_env, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    lines = _run(bundle_env, expect_calls=False)
    assert bundle_env["calls"] == []
    assert lines == []


def test_missing_agent_runtime_is_a_silent_no_op(monkeypatch):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    for m in ("agent_runtime.aicf_worker", "agent_runtime.config", "agent_runtime.hardware"):
        monkeypatch.delitem(sys.modules, m, raising=False)

    import builtins

    real_import = builtins.__import__

    def _blocked(name, *a, **kw):
        if name.startswith("agent_runtime"):
            raise ImportError("no agent_runtime in this install")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    console = _Console()
    upmod._ensure_aicf_bundle(console)  # must not raise
    assert console.lines == []
