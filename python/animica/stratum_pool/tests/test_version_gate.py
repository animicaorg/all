"""Tests for pool-level miner-version enforcement (Phase 5).

Covers the pure version logic, the share validator short-circuit for rejected
miners, and the server's authorize-time gate. Policy only — no consensus.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from animica.stratum_pool import version_gate as vg
from animica.stratum_pool.stratum_server import PoolShareValidator, StratumPoolServer


# ---------------------------------------------------------------------------
# pure version logic
# ---------------------------------------------------------------------------

def test_parse_version_variants():
    assert vg.parse_version("0.5.0") == (0, 5, 0)
    assert vg.parse_version("v0.5") == (0, 5)
    assert vg.parse_version("0.5.0-rc1") == (0, 5, 0)
    assert vg.parse_version("0.5.0+build9") == (0, 5, 0)
    assert vg.parse_version("") is None
    assert vg.parse_version(None) is None
    assert vg.parse_version("garbage") is None


def test_version_ge_padding_and_edges():
    assert vg.version_ge("0.5.0", "0.5") is True       # padded equal
    assert vg.version_ge("0.5", "0.5.0") is True
    assert vg.version_ge("0.6.0", "0.5.0") is True
    assert vg.version_ge("0.4.10", "0.5.0") is False    # numeric, not lexical
    assert vg.version_ge(None, "0.5.0") is False        # no version ⇒ too old
    assert vg.version_ge("0.4.0", "") is True           # no minimum ⇒ allow


def test_extract_version_from_features():
    assert vg.extract_version({"version": "0.5.0"}) == "0.5.0"
    assert vg.extract_version({"agent_version": "0.5.1"}) == "0.5.1"
    assert vg.extract_version({"aicf": {"version": "0.6.0"}}) == "0.6.0"
    assert vg.extract_version({"aicf": {"tiers": ["x"]}}) == ""
    assert vg.extract_version({}) == ""
    assert vg.extract_version(None) == ""


def test_evaluate_enforcement_modes():
    feats_old = {"aicf": {"tiers": ["standard"]}}        # no version → old
    feats_new = {"version": "0.5.0"}
    # enforcement off ⇒ always ok (but still surfaces reported version)
    assert vg.evaluate(feats_new, "0.5.0", enforce=False)["ok"] is True
    # no minimum set ⇒ inert even when enforce=True
    assert vg.evaluate(feats_old, "", enforce=True)["enforced"] is False
    # enforced + old ⇒ rejected with a reason
    v = vg.evaluate(feats_old, "0.5.0", enforce=True)
    assert v["ok"] is False and v["enforced"] is True and v["reason"]
    # enforced + new ⇒ ok
    assert vg.evaluate(feats_new, "0.5.0", enforce=True)["ok"] is True


# ---------------------------------------------------------------------------
# share validator short-circuits rejected miners (before touching the adapter)
# ---------------------------------------------------------------------------

class _ExplodingAdapter:
    async def validate_and_submit_share(self, *a, **k):  # pragma: no cover
        raise AssertionError("rejected miner must never reach share validation")


def test_validator_rejects_flagged_address():
    rejected = {"anim1old"}
    v = PoolShareValidator(_ExplodingAdapter(),
                           is_rejected=lambda a: a in rejected)
    ok, reason, is_block, _ = asyncio.run(
        v.validate(None, {"_address": "anim1old"}))
    assert ok is False and "too old" in reason and is_block is False


# ---------------------------------------------------------------------------
# server authorize-time gate
# ---------------------------------------------------------------------------

def _bare_server(min_version: str, enforce: bool) -> StratumPoolServer:
    srv = StratumPoolServer.__new__(StratumPoolServer)  # skip socket-binding init
    srv._config = SimpleNamespace(min_miner_version=min_version,
                                  require_min_version=enforce)
    srv._version_rejected = set()
    srv._log = logging.getLogger("test.version_gate")
    return srv


def test_server_gate_rejects_old_and_admits_new():
    srv = _bare_server("0.5.0", enforce=True)

    old = SimpleNamespace(address="anim1old", authorized=True)
    assert srv._enforce_version(old, {"aicf": {"tiers": ["standard"]}}) is False
    assert old.authorized is False                  # de-authorized
    assert srv._is_version_rejected("anim1old") is True

    new = SimpleNamespace(address="anim1new", authorized=True)
    assert srv._enforce_version(new, {"version": "0.5.0"}) is True
    assert srv._is_version_rejected("anim1new") is False


def test_server_gate_inert_when_disabled():
    srv = _bare_server("", enforce=False)
    s = SimpleNamespace(address="anim1any", authorized=True)
    # no minimum + not enforcing ⇒ everyone passes, nothing rejected
    assert srv._enforce_version(s, {}) is True
    assert srv._version_rejected == set()
