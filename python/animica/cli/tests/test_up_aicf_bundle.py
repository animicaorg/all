"""`animica up` installs a model bundle so the node can actually serve.

The live network had ZERO serving workers because every machine registered with
``tiers: []``: AICFWorker drops any tier without a bundle in
``$ANIMICA_DATA_DIR/models/<tier>/*/{manifest,inference}.json``, and `up` only
ever PRINTED "run aicf-worker pull" instead of doing it. These tests pin the
behaviour that fixes that, and — just as importantly — pin the cases where it
must NOT download, since the bundle is multiple gigabytes.

Nothing here touches the network: agent_runtime.aicf_worker is stubbed.
"""
from __future__ import annotations

import sys
import time
import types

import pytest

from animica.cli import up as upmod


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, s) -> None:  # noqa: ANN001 - rich-compatible shim
        self.lines.append(str(s))


_ENV_KEYS = (
    "ANIMICA_AICF_NO_AUTOPULL",
    "ANIMICA_AICF_PIPELINE_MODEL_ID",
    "ANIMICA_AICF_ADVERTISE_WITHOUT_BUNDLE",
    "ANIMICA_AICF_AUTOPULL_TIER",
)


@pytest.fixture
def bundle_env(monkeypatch):
    """Stub agent_runtime and return a knob-set for each scenario."""
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)

    state = {"has_bundle": False, "fail": False, "calls": []}

    fake = types.ModuleType("agent_runtime.aicf_worker")
    fake._has_servable_bundle = lambda tier: state["has_bundle"]

    def _bootstrap(tier, **_kw):
        state["calls"].append(tier)
        if state["fail"]:
            raise RuntimeError("network down")
        return f"/root/.animica/models/{tier}/hf-x"

    fake.bootstrap_bundle_from_hf = _bootstrap
    monkeypatch.setitem(sys.modules, "agent_runtime.aicf_worker", fake)
    return state


def _run(state):
    console = _Console()
    upmod._ensure_aicf_bundle(console)
    # the fetch runs on a daemon thread so `up` is never blocked by a
    # multi-GB download; give it a moment to land before asserting
    for _ in range(100):
        if state["calls"]:
            break
        time.sleep(0.01)
    time.sleep(0.05)
    return console.lines


def test_missing_bundle_is_fetched_at_the_smallest_tier(bundle_env):
    lines = _run(bundle_env)
    assert bundle_env["calls"] == ["tiny"], (
        "must default to the SMALLEST catalog tier — the goal is to make the node "
        "servable at all, not to pull the largest model the hardware could run"
    )
    assert any("fetching the 'tiny' tier" in ln for ln in lines)
    assert any("bundle ready" in ln for ln in lines)


def test_existing_bundle_is_a_silent_no_op(bundle_env):
    bundle_env["has_bundle"] = True
    lines = _run(bundle_env)
    assert bundle_env["calls"] == [], "every subsequent `up` must not re-download"
    assert lines == [], "and must not nag about it either"


@pytest.mark.parametrize(
    "key,value",
    [
        ("ANIMICA_AICF_NO_AUTOPULL", "1"),
        # Pipeline / bare-capacity modes serve WITHOUT a local bundle, so a
        # download would be pure waste.
        ("ANIMICA_AICF_PIPELINE_MODEL_ID", "some-model"),
        ("ANIMICA_AICF_ADVERTISE_WITHOUT_BUNDLE", "1"),
    ],
)
def test_opt_outs_skip_the_download(bundle_env, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    lines = _run(bundle_env)
    assert bundle_env["calls"] == [], f"{key} must prevent any fetch"
    assert lines == []


def test_tier_is_overridable(bundle_env, monkeypatch):
    monkeypatch.setenv("ANIMICA_AICF_AUTOPULL_TIER", "small")
    _run(bundle_env)
    assert bundle_env["calls"] == ["small"]


def test_a_failed_download_never_breaks_up(bundle_env):
    bundle_env["fail"] = True
    lines = _run(bundle_env)  # must not raise
    assert bundle_env["calls"] == ["tiny"]
    assert any("fetch failed" in ln for ln in lines), (
        "a failure has to be reported, not swallowed — otherwise the operator "
        "is back to a silently idle worker"
    )


def test_missing_agent_runtime_is_a_silent_no_op(monkeypatch):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delitem(sys.modules, "agent_runtime.aicf_worker", raising=False)

    import builtins

    real_import = builtins.__import__

    def _blocked(name, *a, **kw):
        if name == "agent_runtime.aicf_worker":
            raise ImportError("no agent_runtime in this install")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    console = _Console()
    upmod._ensure_aicf_bundle(console)  # must not raise
    assert console.lines == []
