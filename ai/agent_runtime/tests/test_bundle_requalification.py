"""The AICF worker must wait for its bundle instead of giving up on it.

`animica up` starts the model download in a background thread and constructs the
worker immediately afterwards, so "no servable bundle" is the NORMAL state on a fresh
miner for as long as the download takes — ~30s for the tiny tier, many minutes for a
34GB flagship. The worker used to qualify tiers exactly once and `return` from run()
when the set was empty, which killed the worker thread seconds before its own bundle
finished landing: `animica up` reported "worker idle (no installed model bundle)"
forever, AICF jobs failed with "no installed flagship bundle", and only a restart
fixed it.

These tests pin the two properties that make that impossible to regress:
  1. a bundle appearing AFTER startup is picked up, with no restart;
  2. the tier set is still bundle-gated, so a worker never advertises capacity it
     cannot serve (the defect the original qualification existed to prevent).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "ai/agent_runtime/src")
from agent_runtime.aicf_worker import _has_servable_bundle, _env_float  # noqa: E402


def _install_bundle(root: Path, tier: str, name: str = "hf-test") -> Path:
    """Write the minimal on-disk shape _has_servable_bundle accepts."""
    d = root / "models" / tier / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    (d / "inference.json").write_text("{}", encoding="utf-8")
    return d


def test_bundle_detection_flips_when_the_download_lands(tmp_path, monkeypatch):
    """The race, in miniature: absent at construction time, present moments later."""
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))

    # t=0: `up` has just started the download. Nothing on disk yet.
    assert _has_servable_bundle("tiny") is False

    # t=30s: the prefetch thread finishes writing the bundle.
    _install_bundle(tmp_path, "tiny")
    assert _has_servable_bundle("tiny") is True, (
        "a bundle written after startup must be detectable — re-qualification "
        "depends on this being a live check and not a cached answer"
    )


def test_a_half_written_bundle_is_not_servable(tmp_path, monkeypatch):
    """A download in progress must not qualify. Both files are required, because
    claiming a job against a partial bundle fails at execution time — after the
    worker has already taken the job off the queue."""
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    d = tmp_path / "models" / "tiny" / "hf-partial"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    assert _has_servable_bundle("tiny") is False, "manifest without inference.json"

    (d / "inference.json").write_text("{}", encoding="utf-8")
    assert _has_servable_bundle("tiny") is True


def test_only_the_installed_tier_qualifies(tmp_path, monkeypatch):
    """Installing `tiny` must not make `flagship` servable. Advertising a tier with
    no bundle is what produced "no installed flagship bundle for tier='flagship'"
    at job-execution time."""
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    _install_bundle(tmp_path, "tiny")
    assert _has_servable_bundle("tiny") is True
    for other in ("small", "flagship", "large"):
        assert _has_servable_bundle(other) is False, other


def test_requalify_interval_is_overridable_and_never_zero(monkeypatch):
    """A zero or garbage interval would turn the re-qualification wait into a busy
    loop hammering the filesystem, so unusable values must fall back."""
    monkeypatch.delenv("ANIMICA_AICF_REQUALIFY_SECS", raising=False)
    assert _env_float("ANIMICA_AICF_REQUALIFY_SECS", 30.0) == 30.0

    monkeypatch.setenv("ANIMICA_AICF_REQUALIFY_SECS", "5")
    assert _env_float("ANIMICA_AICF_REQUALIFY_SECS", 30.0) == 5.0

    for bad in ("0", "-1", "", "abc"):
        monkeypatch.setenv("ANIMICA_AICF_REQUALIFY_SECS", bad)
        assert _env_float("ANIMICA_AICF_REQUALIFY_SECS", 30.0) == 30.0, bad


def test_servable_tiers_recomputes_from_disk(tmp_path, monkeypatch):
    """_servable_tiers must re-read the filesystem each call. Bound to a stub rather
    than a real AICFWorker so the test needs no config, node or network."""
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    from agent_runtime.aicf_worker import AICFWorker

    class _Stub:
        _eligible_tiers = ["tiny", "flagship"]
        _skip_bundle_qual = False
        _servable_tiers = AICFWorker._servable_tiers

    stub = _Stub()
    assert stub._servable_tiers() == []

    _install_bundle(tmp_path, "flagship")
    assert stub._servable_tiers() == ["flagship"]

    _install_bundle(tmp_path, "tiny")
    assert sorted(stub._servable_tiers()) == ["flagship", "tiny"]


def test_override_advertises_without_a_bundle(tmp_path, monkeypatch):
    """ANIMICA_AICF_ADVERTISE_WITHOUT_BUNDLE / pipeline mode deliberately bypass the
    gate — those workers serve via the layer-range path, not a local bundle."""
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    from agent_runtime.aicf_worker import AICFWorker

    class _Stub:
        _eligible_tiers = ["tiny", "flagship"]
        _skip_bundle_qual = True
        _servable_tiers = AICFWorker._servable_tiers

    assert _Stub()._servable_tiers() == ["tiny", "flagship"]


# --- chain-tier vs catalog-tier vocabulary ----------------------------------
# Jobs carry CHAIN tiers (free/standard/premium/elite); bundles install under
# CATALOG tiers (tiny/small/flagship/large). A worker handed a `standard` job used to
# look for models/standard, which nothing ever creates — so it claimed the job, threw
# BundleError, and never returned a result.

def test_a_standard_job_is_served_by_the_small_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    _install_bundle(tmp_path, "small")          # what `animica up` creates
    assert _has_servable_bundle("small") is True
    assert _has_servable_bundle("standard") is True, (
        "a chain-tier job must resolve to the catalog-tier bundle on disk"
    )


def test_every_chain_tier_maps_to_its_catalog_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    from agent_runtime.aicf_worker import bundle_dir_candidates

    for chain, catalog in (("free", "tiny"), ("standard", "small"),
                           ("premium", "flagship"), ("elite", "large")):
        assert catalog in bundle_dir_candidates(chain), (chain, catalog)
        assert chain in bundle_dir_candidates(catalog), (catalog, chain)


def test_tiers_still_do_not_bleed_into_each_other(tmp_path, monkeypatch):
    """Mapping must not make everything servable. Installing `small` serves
    standard/small only — a premium/flagship job must still be refused."""
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path))
    _install_bundle(tmp_path, "small")
    assert _has_servable_bundle("standard") is True
    for other in ("premium", "flagship", "elite", "large", "free", "tiny"):
        assert _has_servable_bundle(other) is False, other


def test_unknown_tier_passes_through_rather_than_vanishing(monkeypatch):
    from agent_runtime.aicf_worker import bundle_dir_candidates
    assert bundle_dir_candidates("some-new-tier") == ["some-new-tier"]
    assert bundle_dir_candidates("") == []


def test_advertised_tiers_cover_both_vocabularies():
    """A worker qualified on `small` must be claimable for `standard` jobs, or the
    queue never offers it anything."""
    from agent_runtime.aicf_worker import advertised_tier_aliases
    out = advertised_tier_aliases(["small"])
    assert "small" in out and "standard" in out
    # No duplicates, order stable, pipeline passes through untouched.
    assert len(out) == len(set(out))
    assert advertised_tier_aliases(["pipeline"]) == ["pipeline"]
