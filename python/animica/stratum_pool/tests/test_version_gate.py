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
    # `is_rejected` returns the REASON (or None), not a boolean: the pool now has two
    # policies — version and inference-serving — and a bare bool meant a miner
    # rejected for serving no inference was told "miner version too old".
    reasons = {"anim1old": "miner version too old — update required"}
    v = PoolShareValidator(_ExplodingAdapter(),
                           is_rejected=lambda a: reasons.get(a))
    ok, reason, is_block, _ = asyncio.run(
        v.validate(None, {"_address": "anim1old"}))
    assert ok is False and "too old" in reason and is_block is False


def test_each_policy_gives_its_own_reason():
    """A miner must be told which rule it broke, or it will 'fix' the wrong thing."""
    srv = StratumPoolServer.__new__(StratumPoolServer)
    srv._version_rejected = {"anim1old"}
    srv._serving_rejected = {"anim1nogpu"}
    assert "version" in srv._is_rejected("anim1old")
    assert "inference serving" in srv._is_rejected("anim1nogpu")
    assert srv._is_rejected("anim1good") is None


def _serving_srv(enforce: bool, *, served: bool = False):
    """A bare server with the serving gate configured and the registry stubbed."""
    srv = StratumPoolServer.__new__(StratumPoolServer)
    srv._config = SimpleNamespace(require_inference_serving=enforce)
    srv._serving_rejected = set()
    srv._served_cache = {}
    srv._log = logging.getLogger("test.serving_gate")

    class _Adapter:
        async def _rpc_call(self, method, params):
            assert method == "aicf.workerStatus"
            return {"jobs_completed": 952 if served else 0}

    srv._adapter = _Adapter()
    return srv


def test_a_miner_that_has_actually_served_is_admitted_even_advertising_nothing():
    """MEASURED on the live registry: the worker with 952 completed jobs advertises
    tiers=[], because a claim carries the tier from the CALLER's request, not from
    what the worker announced. Gating on the advertisement alone bans the network's
    most productive server."""
    srv = _serving_srv(True, served=True)
    sess = SimpleNamespace(address="anim1busyserver", authorized=True)
    assert asyncio.run(srv._enforce_inference_serving(sess, {"aicf": {"tiers": []}})) is True
    assert sess.authorized is True
    assert srv._serving_rejected == set()


def test_a_failed_registry_probe_admits_rather_than_bans():
    """Refusing to pay someone because an RPC timed out is the worse error."""
    srv = _serving_srv(True, served=False)

    class _Broken:
        async def _rpc_call(self, *a, **k):
            raise RuntimeError("node unreachable")

    srv._adapter = _Broken()
    sess = SimpleNamespace(address="anim1unknown", authorized=True)
    assert asyncio.run(srv._enforce_inference_serving(sess, {})) is True
    assert sess.authorized is True


def test_serving_gate_is_off_by_default_and_admits_a_non_serving_miner():
    """DEFAULT OFF is load-bearing: zero workers advertise a tier today, so enabling
    it would reject every connected miner and stop the pool producing blocks."""
    srv = _serving_srv(False)
    s2 = SimpleNamespace(address="anim1nogpu", authorized=True)
    assert asyncio.run(srv._enforce_inference_serving(s2, {})) is True
    assert s2.authorized is True
    assert srv._serving_rejected == set()


def test_serving_gate_when_enabled_refuses_a_miner_that_serves_nothing():
    # served=False: the registry agrees it has never served, so nothing rescues it.
    srv = _serving_srv(True, served=False)

    # No aicf key at all, an empty list, and a list of blanks all mean "serves nothing".
    for features in ({}, {"aicf": {}}, {"aicf": {"tiers": []}}, {"aicf": {"tiers": ["", " "]}}):
        sess = SimpleNamespace(address="anim1nogpu", authorized=True)
        assert asyncio.run(srv._enforce_inference_serving(sess, features)) is False, features
        assert sess.authorized is False
    assert "anim1nogpu" in srv._serving_rejected

    # Advertising a real tier admits it AND clears the earlier rejection.
    good = SimpleNamespace(address="anim1nogpu", authorized=True)
    assert asyncio.run(srv._enforce_inference_serving(good, {"aicf": {"tiers": ["standard"]}})) is True
    assert good.authorized is True
    assert "anim1nogpu" not in srv._serving_rejected


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
