from dataclasses import replace
from typing import Any

import pytest

from core.types.header import Header, serialize_header
from core.utils.hash import sha3_256
from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION
from rpc.methods import miner as miner_methods
from rpc.tests import new_test_client, rpc_call


def _hex_to_bytes(value: str) -> bytes:
    hex_value = value[2:] if value.startswith("0x") else value
    if len(hex_value) % 2:
        hex_value = "0" + hex_value
    return bytes.fromhex(hex_value)


def _header_from_work_header(header_view: dict) -> Header:
    return Header(
        v=int(header_view.get("v", 1)),
        chainId=int(header_view.get("chainId", header_view.get("chain_id", 0))),
        height=int(header_view.get("height", header_view.get("number", 0))),
        parentHash=_hex_to_bytes(header_view.get("parentHash", "0x" + "00" * 32)),
        timestamp=int(header_view.get("timestamp", 0)),
        stateRoot=_hex_to_bytes(header_view.get("stateRoot", "0x" + "00" * 32)),
        txsRoot=_hex_to_bytes(header_view.get("txsRoot", "0x" + "00" * 32)),
        receiptsRoot=_hex_to_bytes(header_view.get("receiptsRoot", "0x" + "00" * 32)),
        proofsRoot=_hex_to_bytes(header_view.get("proofsRoot", "0x" + "00" * 32)),
        daRoot=_hex_to_bytes(header_view.get("daRoot", "0x" + "00" * 32)),
        mixSeed=_hex_to_bytes(header_view.get("mixSeed", "0x" + "00" * 32)),
        poiesPolicyRoot=_hex_to_bytes(
            header_view.get("poiesPolicyRoot", "0x" + "00" * 32)
        ),
        pqAlgPolicyRoot=_hex_to_bytes(
            header_view.get("pqAlgPolicyRoot", "0x" + "00" * 32)
        ),
        thetaMicro=int(
            header_view.get(
                "thetaMicro",
                header_view.get("thetaTargetMicro", header_view.get("theta_target_micro", 0)),
            )
        ),
        workType=int(header_view.get("workType", header_view.get("work_type", 0) or 0)),
        nonce=int(header_view.get("nonce", 0)),
        extra=_hex_to_bytes(header_view.get("extra", "0x")),
    )


def _find_nonce(work: dict) -> str:
    target = int(work["target"], 16)
    header = _header_from_work_header(work["header"])
    for i in range(10000):
        candidate = replace(header, nonce=i)
        digest = sha3_256(serialize_header(candidate))
        if int.from_bytes(digest, "big") <= target:
            return "0x" + format(i, "x")
    pytest.skip("could not find a satisfying nonce within search space")


def _snapshot_miner_globals() -> dict[str, dict]:
    with miner_methods._HEAD_RW_LOCK:
        with miner_methods._TEMPLATE_CACHE_LOCK:
            return {
                "job_cache": dict(miner_methods._JOB_CACHE),
                "template_cache": dict(miner_methods._TEMPLATE_CACHE),
                "local_head": dict(miner_methods._LOCAL_HEAD),
                "head_state": dict(miner_methods._HEAD_STATE),
            }


def _restore_miner_globals(snapshot: dict[str, dict]) -> None:
    with miner_methods._HEAD_RW_LOCK:
        miner_methods._JOB_CACHE.clear()
        miner_methods._JOB_CACHE.update(snapshot["job_cache"])
        miner_methods._LOCAL_HEAD.clear()
        miner_methods._LOCAL_HEAD.update(snapshot["local_head"])
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update(snapshot["head_state"])
        with miner_methods._TEMPLATE_CACHE_LOCK:
            miner_methods._TEMPLATE_CACHE.clear()
            miner_methods._TEMPLATE_CACHE.update(snapshot["template_cache"])


def test_mempool_pending_count_uses_snapshot_total():
    class _Snapshot:
        entries = [object()]
        total = 4096

    class _Mempool:
        def snapshot(self, *, limit: int = 1000):
            return _Snapshot()

    assert miner_methods._mempool_pending_count(_Mempool()) == 4096


def test_get_work_returns_template():
    client, cfg, _ = new_test_client()
    res = rpc_call(client, "miner.getWork")
    job = res["result"]
    assert job["height"] >= 1
    assert "header" in job and isinstance(job["header"], dict)
    assert job["header"].get("number") == job["height"]
    assert "thetaMicro" in job
    assert "shareTarget" in job
    assert job["algo"] == "asic_sha256"
    assert "jobId" in job and job["jobId"] in miner_methods._JOB_CACHE


def test_get_work_accepts_explicit_empty_params():
    client, _, _ = new_test_client()

    res = rpc_call(client, "miner.getWork", [])
    job = res["result"]

    assert job["jobId"] in miner_methods._JOB_CACHE


def test_get_block_template_accepts_address_param(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    client, _, _ = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    res = rpc_call(client, "miner.getBlockTemplate", {"address": payout_address})

    assert res["result"]["coinbase"]["address"] == payout_address
    assert res["result"]["address"] == payout_address
    assert res["result"]["payout_address"] == payout_address


def test_get_block_template_accepts_payout_address_alias(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    client, _, _ = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    res = rpc_call(client, "miner.getBlockTemplate", {"payout_address": payout_address})

    assert res["result"]["coinbase"]["address"] == payout_address
    assert res["result"]["address"] == payout_address
    assert res["result"]["payout_address"] == payout_address


def test_get_block_template_accepts_positional_address(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    client, _, _ = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    res = rpc_call(client, "miner.getBlockTemplate", [payout_address])

    assert res["result"]["coinbase"]["address"] == payout_address
    assert res["result"]["address"] == payout_address
    assert res["result"]["payout_address"] == payout_address


def test_get_block_template_requires_address():
    client, _, _ = new_test_client()

    res = rpc_call(client, "miner.getBlockTemplate", {}, expect_error=True)

    assert res["error"]["code"] == -32602
    assert res["error"]["data"]["detail"] == "address is required"


def test_get_block_template_rejects_invalid_address():
    client, _, _ = new_test_client()

    res = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": "not-a-valid-address"},
        expect_error=True,
    )

    assert res["error"]["code"] == -32602
    assert (
        "address must be a 32-byte 0x-prefixed hex or anim bech32 address"
        in res["error"]["data"]["detail"]
    )


def test_jsonrpc_endpoint_accepts_empty_params_via_post_body():
    """Mimic the curl call with params: [] hitting the /rpc endpoint directly."""

    client, _, _ = new_test_client()

    payload = {"jsonrpc": "2.0", "id": 3, "method": "miner.getWork", "params": []}
    res = client.post("/rpc", json=payload)

    assert res.status_code == 200
    data = res.json()
    assert data.get("error") is None
    assert data.get("result") is not None
    assert data["result"].get("jobId") in miner_methods._JOB_CACHE


def test_get_work_handles_callable_header_hash():
    """Ensure headers exposing hash() methods don't break parent hash resolution."""

    client, _, _ = new_test_client()

    prev_head = dict(miner_methods._LOCAL_HEAD)
    try:
        header = Header(
            v=1,
            chainId=1,
            height=1,
            parentHash=miner_methods.ZERO32,
            timestamp=0,
            stateRoot=miner_methods.ZERO32,
            txsRoot=miner_methods.ZERO32,
            receiptsRoot=miner_methods.ZERO32,
            proofsRoot=miner_methods.ZERO32,
            daRoot=miner_methods.ZERO32,
            mixSeed=miner_methods.ZERO32,
            poiesPolicyRoot=miner_methods.ZERO32,
            pqAlgPolicyRoot=miner_methods.ZERO32,
            thetaMicro=miner_methods._resolve_theta(),
            nonce=0,
            extra=b"",
        )
        miner_methods._LOCAL_HEAD.update({"height": 5, "hash": None, "header": header})

        res = rpc_call(client, "miner.getWork")
        assert res["result"]["jobId"] in miner_methods._JOB_CACHE
    finally:
        miner_methods._LOCAL_HEAD.clear()
        miner_methods._LOCAL_HEAD.update(prev_head)


def test_submit_work_accepts_valid_solution_and_updates_head():
    client, _, _ = new_test_client()
    head_before = rpc_call(client, "chain.getHead")["result"]
    job = rpc_call(client, "miner.getWork")["result"]

    nonce_hex = _find_nonce(job)
    res = rpc_call(
        client, "miner.submitWork", {"jobId": job["jobId"], "nonce": nonce_hex}
    )

    result = res["result"]
    assert result["accepted"] is True
    assert result["reason"] is None
    assert int(result["height"]) >= int(job["height"])
    assert isinstance(result.get("newHead"), dict)
    assert result["newHead"].get("hash")
    assert int(result["newHead"].get("height", 0)) >= int(job["height"])
    assert result.get("expected_reward") is not None
    assert result.get("credited_amount") is not None
    assert result.get("block_hash")

    head_after = rpc_call(client, "chain.getHead")["result"]
    assert int(head_after.get("height", 0)) >= int(head_before.get("height", 0)) + 1
    assert head_after.get("hash") == result["newHead"].get("hash")
    assert miner_methods._LOCAL_HEAD == {}


def test_submit_work_accepts_positional_params():
    client, _, _ = new_test_client()
    job = rpc_call(client, "miner.getWork", ["asic_sha256"])["result"]

    nonce_hex = _find_nonce(job)
    res = rpc_call(client, "miner.submitWork", [job["jobId"], nonce_hex])

    result = res["result"]
    assert result["accepted"] is True


def test_submit_work_hash_and_reporting_aligned_with_canonical_header(
    monkeypatch: pytest.MonkeyPatch,
):
    from mining.templates import HeaderTemplate, MiningJob

    header_tpl = HeaderTemplate(
        parent_hash=b"\x11" * 32,
        number=5,
        chain_id=1337,
        state_root=b"\x00" * 32,
        txs_root=b"\x00" * 32,
        receipts_root=b"\x00" * 32,
        proofs_root=b"\x00" * 32,
        da_root=b"\x00" * 32,
        theta_target_micro=1_000_000,
        mix_seed=b"\x22" * 32,
        pq_alg_policy_root=b"\x00" * 32,
        poies_policy_root=b"\x00" * 32,
        timestamp=1_700_000_000,
        work_type=0,
        extra=b"\x01\x02",
    )
    job_obj = MiningJob(
        job_id="job-canonical",
        parent_hash=header_tpl.parent_hash,
        parent_height=4,
        chain_id=header_tpl.chain_id,
        target=(1 << 256) - 1,
        theta_target_micro=header_tpl.theta_target_micro,
        proof_type="sha256d",
        challenge=None,
        expires_at=None,
        template_version=1,
        header=header_tpl,
        sign_bytes=header_tpl.to_sign_bytes(),
    )

    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._JOB_CACHE.clear()
        miner_methods._JOB_CACHE["job-canonical"] = {
            "job": job_obj,
            "sign_bytes": job_obj.sign_bytes,
            "mix_seed": job_obj.header.mix_seed,
            "block_target": (1 << 256) - 1,
            "height": int(job_obj.header.number),
            "created_at": 0.0,
            "parent_hash": job_obj.parent_hash,
            "parent_height": job_obj.parent_height,
            "chain_id": int(job_obj.chain_id),
            "head_generation": 7,
        }

        seen: dict[str, Any] = {}
        calls = {"head": 0}

        def _fake_head_snapshot():
            calls["head"] += 1
            if calls["head"] == 1:
                return {
                    "height": 4,
                    "hash": "0x" + ("11" * 32),
                    "generation": 7,
                }
            return {
                "height": 5,
                "hash": "0x" + ("22" * 32),
                "generation": 8,
            }

        def _fake_submit_block(block_payload: dict[str, Any]):
            seen["payload"] = block_payload
            return {
                "accepted": True,
                "duplicate": False,
                "expected_reward": 500,
                "credited_amount": 500,
                "credited_delta": 500,
                "credited_source": "state_balance_delta",
                "balance_before": 1000,
                "balance_now": 1500,
                "block_hash": "0x" + ("aa" * 32),
            }

        monkeypatch.setattr(miner_methods, "_current_head_snapshot", _fake_head_snapshot)
        monkeypatch.setattr(miner_methods, "miner_submit_block", _fake_submit_block)

        result = miner_methods.miner_submit_work(jobId="job-canonical", nonce="0x2a")

        expected_header = miner_methods._header_from_cached_job(
            {"job": job_obj},
            nonce=0x2A,
        )
        expected_hash = "0x" + expected_header.hash().hex()

        assert result["accepted"] is True
        assert result["hash"] == expected_hash
        assert result["newHead"] == {"height": 5, "hash": "0x" + ("22" * 32)}
        assert result["expected_reward"] == 500
        assert result["credited_amount"] == 500
        assert result["credited_delta"] == 500
        assert result["balance_before"] == 1000
        assert result["balance_now"] == 1500
        assert result["credited_source"] == "state_balance_delta"
        assert seen["payload"]["header"]["nonce"] == 0x2A
        assert seen["payload"]["parentHash"] == "0x" + ("11" * 32)
    finally:
        _restore_miner_globals(snapshot)


def test_submit_work_uses_submit_result_head_when_snapshot_lags(
    monkeypatch: pytest.MonkeyPatch,
):
    from mining.templates import HeaderTemplate, MiningJob

    header_tpl = HeaderTemplate(
        parent_hash=b"\x44" * 32,
        number=9,
        chain_id=1337,
        state_root=b"\x00" * 32,
        txs_root=b"\x00" * 32,
        receipts_root=b"\x00" * 32,
        proofs_root=b"\x00" * 32,
        da_root=b"\x00" * 32,
        theta_target_micro=1_000_000,
        mix_seed=b"\x55" * 32,
        pq_alg_policy_root=b"\x00" * 32,
        poies_policy_root=b"\x00" * 32,
        timestamp=1_700_000_000,
        work_type=0,
        extra=b"",
    )
    job_obj = MiningJob(
        job_id="job-lagged-head",
        parent_hash=header_tpl.parent_hash,
        parent_height=8,
        chain_id=header_tpl.chain_id,
        target=(1 << 256) - 1,
        theta_target_micro=header_tpl.theta_target_micro,
        proof_type="sha256d",
        challenge=None,
        expires_at=None,
        template_version=1,
        header=header_tpl,
        sign_bytes=header_tpl.to_sign_bytes(),
    )

    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._JOB_CACHE.clear()
        miner_methods._JOB_CACHE["job-lagged-head"] = {
            "job": job_obj,
            "sign_bytes": job_obj.sign_bytes,
            "mix_seed": job_obj.header.mix_seed,
            "block_target": (1 << 256) - 1,
            "height": int(job_obj.header.number),
            "created_at": 0.0,
            "parent_hash": job_obj.parent_hash,
            "parent_height": job_obj.parent_height,
            "chain_id": int(job_obj.chain_id),
            "head_generation": 21,
        }

        calls = {"head": 0}

        def _fake_head_snapshot():
            calls["head"] += 1
            # Submission-time canonical head (for stale checks).
            if calls["head"] == 1:
                return {
                    "height": 8,
                    "hash": "0x" + ("44" * 32),
                    "generation": 21,
                }
            # Simulate a lagging snapshot read after accept.
            return {
                "height": 8,
                "hash": "0x" + ("44" * 32),
                "generation": 21,
            }

        def _fake_submit_block(_block_payload: dict[str, Any]):
            return {
                "accepted": True,
                "newHead": {"height": 9, "hash": "0x" + ("66" * 32)},
                "new_head": 9,
                "new_head_hash": "0x" + ("66" * 32),
                "block_hash": "0x" + ("66" * 32),
                "expected_reward": 700,
                "credited_amount": 700,
            }

        monkeypatch.setattr(miner_methods, "_current_head_snapshot", _fake_head_snapshot)
        monkeypatch.setattr(miner_methods, "miner_submit_block", _fake_submit_block)

        result = miner_methods.miner_submit_work(jobId="job-lagged-head", nonce="0x1")

        assert result["accepted"] is True
        assert result["newHead"] == {"height": 9, "hash": "0x" + ("66" * 32)}
        assert result["new_head"] == 9
        assert result["new_head_hash"] == "0x" + ("66" * 32)
        assert result["height"] == 9
    finally:
        _restore_miner_globals(snapshot)


def test_submit_work_forwards_cached_template_transactions(
    monkeypatch: pytest.MonkeyPatch,
):
    from mining.templates import HeaderTemplate, MiningJob

    header_tpl = HeaderTemplate(
        parent_hash=b"\x11" * 32,
        number=5,
        chain_id=1337,
        state_root=b"\x00" * 32,
        txs_root=b"\x00" * 32,
        receipts_root=b"\x00" * 32,
        proofs_root=b"\x00" * 32,
        da_root=b"\x00" * 32,
        theta_target_micro=1_000_000,
        mix_seed=b"\x22" * 32,
        pq_alg_policy_root=b"\x00" * 32,
        poies_policy_root=b"\x00" * 32,
        timestamp=1_700_000_000,
        work_type=0,
        extra=b"",
    )
    job_obj = MiningJob(
        job_id="job-with-template",
        parent_hash=header_tpl.parent_hash,
        parent_height=4,
        chain_id=header_tpl.chain_id,
        target=(1 << 256) - 1,
        theta_target_micro=header_tpl.theta_target_micro,
        proof_type="sha256d",
        challenge=None,
        expires_at=None,
        template_version=1,
        header=header_tpl,
        sign_bytes=header_tpl.to_sign_bytes(),
    )

    header_override = {
        "v": 1,
        "chainId": 1337,
        "height": 5,
        "parentHash": "0x" + ("11" * 32),
        "timestamp": 1_700_000_000,
        "stateRoot": "0x" + ("00" * 32),
        "txsRoot": "0x" + ("00" * 32),
        "receiptsRoot": "0x" + ("00" * 32),
        "proofsRoot": "0x" + ("00" * 32),
        "daRoot": "0x" + ("00" * 32),
        "mixSeed": "0x" + ("22" * 32),
        "poiesPolicyRoot": "0x" + ("00" * 32),
        "pqAlgPolicyRoot": "0x" + ("00" * 32),
        "thetaMicro": 1_000_000,
        "workType": 0,
        "nonce": 0,
        "extra": "0x",
    }

    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._JOB_CACHE.clear()
        miner_methods._JOB_CACHE["job-with-template"] = {
            "job": job_obj,
            "header_override": header_override,
            "sign_bytes": job_obj.sign_bytes,
            "mix_seed": job_obj.header.mix_seed,
            "block_target": (1 << 256) - 1,
            "share_target": 0.01,
            "height": 5,
            "created_at": 0.0,
            "parent_hash": header_tpl.parent_hash,
            "parent_height": 4,
            "chain_id": 1337,
            "head_generation": 7,
            "template_id": "tpl-cache-1",
            "template_txs_raw": ["0xdeadbeef"],
            "template_proofs": [],
        }

        calls = {"head": 0}
        seen: dict[str, Any] = {}

        def _fake_head_snapshot():
            calls["head"] += 1
            if calls["head"] == 1:
                return {
                    "height": 4,
                    "hash": "0x" + ("11" * 32),
                    "generation": 7,
                }
            return {
                "height": 5,
                "hash": "0x" + ("aa" * 32),
                "generation": 8,
            }

        def _fake_submit_block(block_payload: dict[str, Any]):
            seen["payload"] = block_payload
            return {
                "accepted": True,
                "block_hash": "0x" + ("aa" * 32),
                "expected_reward": 0,
                "credited_amount": 0,
            }

        monkeypatch.setattr(miner_methods, "_current_head_snapshot", _fake_head_snapshot)
        monkeypatch.setattr(miner_methods, "miner_submit_block", _fake_submit_block)

        result = miner_methods.miner_submit_work(jobId="job-with-template", nonce="0x2a")

        assert result["accepted"] is True
        assert seen["payload"]["templateId"] == "tpl-cache-1"
        assert seen["payload"]["txs"] == ["0xdeadbeef"]
        assert seen["payload"]["header"]["nonce"] == 0x2A
    finally:
        _restore_miner_globals(snapshot)


def test_get_work_materializes_mempool_template_when_pending(
    monkeypatch: pytest.MonkeyPatch,
):
    class _Cfg:
        chain_id = 1337

    class _Snap:
        entries = [object()]

    class _Mempool:
        def snapshot(self, *, limit: int = 1000):
            return _Snap()

        def has_hash(self, _tx_hash: str) -> bool:
            return False

    class _Ctx:
        cfg = _Cfg()
        block_db = None
        mempool = _Mempool()

        def get_head(self):
            return {
                "height": 0,
                "hash": "0x" + ("11" * 32),
                "header": {"thetaMicro": 1_000_000},
            }

    header_override = {
        "v": 1,
        "chainId": 1337,
        "height": 1,
        "parentHash": "0x" + ("11" * 32),
        "timestamp": 1_700_000_000,
        "stateRoot": "0x" + ("00" * 32),
        "txsRoot": "0x" + ("00" * 32),
        "receiptsRoot": "0x" + ("00" * 32),
        "proofsRoot": "0x" + ("00" * 32),
        "daRoot": "0x" + ("00" * 32),
        "mixSeed": "0x" + ("22" * 32),
        "poiesPolicyRoot": "0x" + ("00" * 32),
        "pqAlgPolicyRoot": "0x" + ("00" * 32),
        "thetaMicro": 1_000_000,
        "workType": 0,
        "nonce": 0,
        "extra": "0x",
    }
    fake_template = {
        "enabled": True,
        "templateId": "tpl-work-1",
        "header": header_override,
        "target": "0x" + ("f" * 64),
        "parent": {"height": 0, "hash": "0x" + ("11" * 32)},
        "txs": [{"hash": "0x" + ("aa" * 32), "raw": "0xdeadbeef"}],
        "proofs": [],
        "mempool": {"pending": 1, "selected": 1, "rejected": {}, "rejectedByHash": {}},
    }

    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update({"height": None, "hash": None, "generation": 0})
        monkeypatch.setattr(miner_methods, "_ctx", lambda: _Ctx())
        monkeypatch.setattr(miner_methods, "_mining_gate", lambda **_kw: (True, None))
        monkeypatch.setattr(miner_methods, "_resolve_mempool_service", lambda _ctx: _Mempool())
        monkeypatch.setattr(miner_methods, "miner_get_block_template", lambda *_a, **_kw: fake_template)

        payout = "0x" + ("33" * 32)
        result = miner_methods.miner_get_work({"address": payout})

        assert result["jobId"] == "tpl-work-1"
        assert result["txCount"] == 1
        assert isinstance(result.get("mempool"), dict)
        assert result["mempool"].get("pending") == 1

        cached = miner_methods._JOB_CACHE.get("tpl-work-1")
        assert isinstance(cached, dict)
        assert cached.get("template_id") == "tpl-work-1"
        assert cached.get("template_txs_raw") == ["0xdeadbeef"]
        assert isinstance(cached.get("header_override"), dict)
    finally:
        _restore_miner_globals(snapshot)


def test_get_work_materializes_template_without_pending_for_theta_updates(
    monkeypatch: pytest.MonkeyPatch,
):
    class _Cfg:
        chain_id = 1337

    class _Snap:
        entries = []

    class _Mempool:
        def snapshot(self, *, limit: int = 1000):
            return _Snap()

        def has_hash(self, _tx_hash: str) -> bool:
            return False

    class _Ctx:
        cfg = _Cfg()
        block_db = None
        mempool = _Mempool()

        def get_head(self):
            return {
                "height": 12,
                "hash": "0x" + ("12" * 32),
                "header": {"thetaMicro": 1_500_000},
            }

    header_override = {
        "v": 1,
        "chainId": 1337,
        "height": 13,
        "parentHash": "0x" + ("12" * 32),
        "timestamp": 1_700_000_120,
        "stateRoot": "0x" + ("00" * 32),
        "txsRoot": "0x" + ("00" * 32),
        "receiptsRoot": "0x" + ("00" * 32),
        "proofsRoot": "0x" + ("00" * 32),
        "daRoot": "0x" + ("00" * 32),
        "mixSeed": "0x" + ("22" * 32),
        "poiesPolicyRoot": "0x" + ("00" * 32),
        "pqAlgPolicyRoot": "0x" + ("00" * 32),
        "thetaMicro": 750_000,
        "workType": 0,
        "nonce": 0,
        "extra": "0x",
    }
    fake_template = {
        "enabled": True,
        "templateId": "tpl-theta-1",
        "header": header_override,
        "target": "0x" + ("e" * 64),
        "parent": {"height": 12, "hash": "0x" + ("12" * 32)},
        "txs": [],
        "proofs": [],
        "mempool": {"pending": 0, "selected": 0, "rejected": {}, "rejectedByHash": {}},
    }

    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update({"height": None, "hash": None, "generation": 0})
        monkeypatch.setattr(miner_methods, "_ctx", lambda: _Ctx())
        monkeypatch.setattr(miner_methods, "_mining_gate", lambda **_kw: (True, None))
        monkeypatch.setattr(miner_methods, "_resolve_mempool_service", lambda _ctx: _Mempool())
        monkeypatch.setattr(miner_methods, "miner_get_block_template", lambda *_a, **_kw: fake_template)

        payout = "0x" + ("44" * 32)
        result = miner_methods.miner_get_work({"address": payout, "include_mempool": False})

        assert result["jobId"] == "tpl-theta-1"
        assert int(result["thetaMicro"]) == 750_000
        assert int(result["txCount"]) == 0

        cached = miner_methods._JOB_CACHE.get("tpl-theta-1")
        assert isinstance(cached, dict)
        assert cached.get("template_id") == "tpl-theta-1"
    finally:
        _restore_miner_globals(snapshot)


def test_get_work_address_param_populates_coinbase_extra_without_client(
    monkeypatch: pytest.MonkeyPatch,
):
    class _Cfg:
        chain_id = 1337

    class _FakeCtx:
        cfg = _Cfg()
        block_db = None

        def get_head(self):
            return {
                "height": 0,
                "hash": "0x" + ("10" * 32),
                "header": {"thetaMicro": 1_000_000},
            }

    payout = "0x" + ("33" * 32)
    payout_bytes = bytes.fromhex("33" * 32)

    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update({"height": None, "hash": None, "generation": 0})
        monkeypatch.setattr(miner_methods, "_ctx", lambda: _FakeCtx())
        monkeypatch.setattr(miner_methods, "_mining_gate", lambda **_kw: (True, None))

        job = miner_methods.miner_get_work({"address": payout})
        extra_hex = job.get("header", {}).get("extra")
        assert isinstance(extra_hex, str) and extra_hex.startswith("0x")
        decoded_extra = miner_methods._decode_header_extra(_hex_to_bytes(extra_hex))
        assert decoded_extra.get("coinbase") == payout_bytes

        cached = miner_methods._JOB_CACHE.get(job["jobId"]) or {}
        assert cached.get("payout_address") == payout
        assert cached.get("payout_address_bytes") == payout_bytes
    finally:
        _restore_miner_globals(snapshot)


def test_submit_work_rejects_invalid_or_stale_jobs():
    client, _, _ = new_test_client()
    job = rpc_call(client, "miner.getWork")["result"]

    # Missing nonce → invalid params
    bad = rpc_call(
        client, "miner.submitWork", {"jobId": job["jobId"]}, expect_error=True
    )
    assert bad["error"]["code"] == -32602

    # Mark cached head generation stale to force stale-head rejection.
    miner_methods._JOB_CACHE[job["jobId"]]["head_generation"] = -1
    stale = rpc_call(
        client,
        "miner.submitWork",
        {"jobId": job["jobId"], "nonce": "0x00"},
    )
    result = stale["result"]
    assert result["accepted"] is False
    assert result["stale"] is True


def test_get_work_binds_to_canonical_parent_head():
    class _Cfg:
        chain_id = 1337

    class _FakeCtx:
        cfg = _Cfg()
        block_db = None

        def get_head(self):
            return {
                "height": 0,
                "hash": "0x" + ("10" * 32),
                "header": {"thetaMicro": 1_000_000},
            }

    fake_ctx = _FakeCtx()
    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update({"height": None, "hash": None, "generation": 0})
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(miner_methods, "_ctx", lambda: fake_ctx)
            mp.setattr(miner_methods, "_mining_gate", lambda **_kw: (True, None))
            job = miner_methods.miner_get_work()
        assert job["parentHash"] == "0x" + ("10" * 32)
        assert int(job["parentHeight"]) == 0
    finally:
        _restore_miner_globals(snapshot)


def test_local_head_cannot_outrun_canonical_head():
    class _Cfg:
        chain_id = 1337

    class _FakeCtx:
        cfg = _Cfg()
        block_db = None

        def __init__(self):
            self.height = 0
            self.hash_hex = "0x" + ("20" * 32)

        def get_head(self):
            return {
                "height": self.height,
                "hash": self.hash_hex,
                "header": {"thetaMicro": 1_000_000},
            }

    fake_ctx = _FakeCtx()
    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update({"height": None, "hash": None, "generation": 0})
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(miner_methods, "_ctx", lambda: fake_ctx)
            mp.setattr(miner_methods, "_mining_gate", lambda **_kw: (True, None))
            first = miner_methods.miner_get_work()
            miner_methods._record_local_block(50, "0x" + ("ff" * 32), None)
            second = miner_methods.miner_get_work()
        assert second["parentHash"] == fake_ctx.hash_hex
        assert int(second["parentHeight"]) == int(fake_ctx.height)
        assert int(second["headGeneration"]) == int(first["headGeneration"])
    finally:
        _restore_miner_globals(snapshot)


def test_record_local_block_does_not_advance_head_state():
    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update(
            {"height": 7, "hash": "0x" + ("11" * 32), "generation": 5}
        )
        miner_methods._JOB_CACHE["job"] = {"height": 8}
        with miner_methods._TEMPLATE_CACHE_LOCK:
            miner_methods._TEMPLATE_CACHE["tpl"] = {"parent_hash": "0x" + ("11" * 32)}

        miner_methods._record_local_block(8, "0x" + ("22" * 32), {"height": 8})

        assert miner_methods._LOCAL_HEAD.get("height") == 8
        assert miner_methods._LOCAL_HEAD.get("hash") == "0x" + ("22" * 32)
        assert miner_methods._HEAD_STATE == {
            "height": 7,
            "hash": "0x" + ("11" * 32),
            "generation": 5,
        }
        assert miner_methods._JOB_CACHE == {}
        with miner_methods._TEMPLATE_CACHE_LOCK:
            assert miner_methods._TEMPLATE_CACHE == {}
    finally:
        _restore_miner_globals(snapshot)


def test_canonical_head_change_invalidates_job_and_template_caches(
    monkeypatch: pytest.MonkeyPatch,
):
    class _FakeCtx:
        def __init__(self):
            self.height = 0
            self.hash_hex = "0x" + ("aa" * 32)

        def get_head(self):
            return {"height": self.height, "hash": self.hash_hex, "header": None}

    fake_ctx = _FakeCtx()
    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update(
            {"height": 0, "hash": fake_ctx.hash_hex, "generation": 12}
        )
        miner_methods._LOCAL_HEAD.clear()
        miner_methods._LOCAL_HEAD.update({"height": 999, "hash": "0x" + ("ff" * 32)})
        miner_methods._JOB_CACHE["j1"] = {"height": 1}
        with miner_methods._TEMPLATE_CACHE_LOCK:
            miner_methods._TEMPLATE_CACHE["t1"] = {"parent_hash": fake_ctx.hash_hex}

        monkeypatch.setattr(miner_methods, "_ctx", lambda: fake_ctx)

        same_head = miner_methods._current_head_snapshot()
        assert same_head["generation"] == 12
        assert "j1" in miner_methods._JOB_CACHE

        fake_ctx.height = 1
        fake_ctx.hash_hex = "0x" + ("bb" * 32)
        moved_head = miner_methods._current_head_snapshot()

        assert moved_head["height"] == 1
        assert moved_head["hash"] == "0x" + ("bb" * 32)
        assert moved_head["generation"] == 13
        assert miner_methods._JOB_CACHE == {}
        assert miner_methods._LOCAL_HEAD == {}
        with miner_methods._TEMPLATE_CACHE_LOCK:
            assert miner_methods._TEMPLATE_CACHE == {}
    finally:
        _restore_miner_globals(snapshot)


def test_get_work_updates_parent_and_generation_after_canonical_block():
    class _Cfg:
        chain_id = 1337

    class _FakeCtx:
        cfg = _Cfg()
        block_db = None

        def __init__(self):
            self.height = 3
            self.hash_hex = "0x" + ("31" * 32)
            self.theta = 1_000_000

        def get_head(self):
            return {
                "height": self.height,
                "hash": self.hash_hex,
                "header": {"thetaMicro": self.theta},
            }

    fake_ctx = _FakeCtx()
    snapshot = _snapshot_miner_globals()
    try:
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update({"height": None, "hash": None, "generation": 0})
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(miner_methods, "_ctx", lambda: fake_ctx)
            mp.setattr(miner_methods, "_mining_gate", lambda **_kw: (True, None))
            before = miner_methods.miner_get_work()

            # Simulate canonical block acceptance.
            fake_ctx.height = 4
            fake_ctx.hash_hex = "0x" + ("32" * 32)
            fake_ctx.theta = 1_250_000
            after = miner_methods.miner_get_work()

        assert after["parentHash"] == fake_ctx.hash_hex
        assert int(after["parentHeight"]) == int(fake_ctx.height)
        assert int(after["headGeneration"]) > int(before["headGeneration"])
        assert int(after["thetaMicro"]) == 1_250_000
    finally:
        _restore_miner_globals(snapshot)


def test_resolve_theta_tracks_canonical_head_theta(
    monkeypatch: pytest.MonkeyPatch,
):
    class _FakeCtx:
        block_db = None

        def __init__(self):
            self.height = 0
            self.hash_hex = "0x" + ("01" * 32)
            self.theta = 1_000_000

        def get_head(self):
            return {
                "height": self.height,
                "hash": self.hash_hex,
                "header": {"thetaMicro": self.theta},
            }

    fake_ctx = _FakeCtx()
    snapshot = _snapshot_miner_globals()
    try:
        monkeypatch.setattr(miner_methods, "_ctx", lambda: fake_ctx)
        miner_methods._HEAD_STATE.clear()
        miner_methods._HEAD_STATE.update({"height": None, "hash": None, "generation": 0})

        theta0 = miner_methods._resolve_theta()
        assert theta0 == 1_000_000

        fake_ctx.height = 1
        fake_ctx.hash_hex = "0x" + ("02" * 32)
        fake_ctx.theta = 1_500_000
        theta1 = miner_methods._resolve_theta()

        assert theta1 == 1_500_000
        assert miner_methods._current_head_snapshot()["generation"] >= 2
    finally:
        _restore_miner_globals(snapshot)


def test_submit_work_rejects_stale_parent():
    client, _, _ = new_test_client()
    job = rpc_call(client, "miner.getWork")["result"]

    miner_methods._JOB_CACHE[job["jobId"]]["parent_hash"] = b"\x01" * 32

    res = rpc_call(
        client,
        "miner.submitWork",
        {"jobId": job["jobId"], "nonce": "0x00"},
    )
    result = res["result"]
    assert result["accepted"] is False
    assert result["stale"] is True
    assert result["reason"] == "stale-parent"


def test_get_work_rejects_wrong_param_type():
    client, _, _ = new_test_client()

    res = rpc_call(client, "miner.getWork", "bad-type", expect_error=True)

    assert res["error"]["code"] == -32602


def test_get_work_disabled_when_stalled(monkeypatch: pytest.MonkeyPatch):
    class _Snap:
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return dict(self._data)

    class _Svc:
        def status_snapshot(self):
            return _Snap({"peers_total": 3})

        def sync_status_snapshot(self):
            return _Snap(
                {"phase": "STALLED", "head_height": 0, "best_header_height": 10}
            )

    import p2p

    monkeypatch.setenv("ANIMICA_MINING_MIN_PEERS", "1")
    monkeypatch.setattr(p2p, "get_service", lambda: _Svc())

    client, _, _ = new_test_client()
    res = rpc_call(client, "miner.getWork")["result"]
    assert res["disabled"] is True
    assert res["reason"] == "sync_phase:stalled"


def test_get_work_disabled_when_100_blocks_behind(monkeypatch: pytest.MonkeyPatch):
    """Test that mining is disabled when 100 blocks or more behind."""
    class _Snap:
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return dict(self._data)

    class _Svc:
        def status_snapshot(self):
            return _Snap({"peers_total": 3, "peers_outbound": 2})

        def sync_status_snapshot(self):
            # exec_head is at height 50, but best_header_height is 150
            # That's 100 blocks behind - should disable mining
            return _Snap(
                {
                    "head_height": 50,
                    "best_header_height": 150,
                    "best_block_height": 50,
                    "fatal_error": None,
                }
            )

    import p2p

    monkeypatch.setenv("ANIMICA_MINING_MIN_PEERS", "1")
    monkeypatch.setattr(p2p, "get_service", lambda: _Svc())

    client, _, _ = new_test_client()
    res = rpc_call(client, "miner.getWork")["result"]
    assert res["disabled"] is True
    assert res["miningEnabled"] is False
    assert res["reason"].startswith("too_far_behind:")
    assert "100_blocks" in res["reason"]


def test_get_work_enabled_when_99_blocks_behind(monkeypatch: pytest.MonkeyPatch):
    """Test that mining is still enabled when 99 blocks behind."""
    class _Snap:
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return dict(self._data)

    class _Svc:
        def status_snapshot(self):
            return _Snap({"peers_total": 3, "peers_outbound": 2})

        def sync_status_snapshot(self):
            # exec_head is at height 50, but best_header_height is 149
            # That's 99 blocks behind - should still allow mining (within threshold)
            return _Snap(
                {
                    "head_height": 50,
                    "best_header_height": 149,
                    "best_block_height": 50,
                    "fatal_error": None,
                }
            )

    import p2p

    monkeypatch.setenv("ANIMICA_MINING_MIN_PEERS", "1")
    # Set max_lag high so it doesn't interfere with this test
    monkeypatch.setenv("ANIMICA_MINING_MAX_LAG", "100")
    monkeypatch.setattr(p2p, "get_service", lambda: _Svc())

    client, _, _ = new_test_client()
    res = rpc_call(client, "miner.getWork")["result"]
    # Should have a job since we're only 99 blocks behind
    assert "jobId" in res
    assert res["miningEnabled"] is True


def test_get_block_template_disabled_when_offline_without_override(
    monkeypatch: pytest.MonkeyPatch,
):
    class _Snap:
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return dict(self._data)

    class _Svc:
        def status_snapshot(self):
            return _Snap({"peers_total": 0, "peers_outbound": 0})

        def sync_status_snapshot(self):
            return _Snap(
                {
                    "head_height": 5,
                    "best_header_height": 5,
                    "best_block_height": 5,
                    "fatal_error": None,
                }
            )

    import p2p

    monkeypatch.delenv("ANIMICA_ALLOW_OFFLINE_MINING_FOR_TESTS", raising=False)
    monkeypatch.setenv("ANIMICA_MINING_MIN_PEERS", "0")
    monkeypatch.setattr(p2p, "get_service", lambda: _Svc())

    client, _, _ = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]
    res = rpc_call(client, "miner.getBlockTemplate", {"address": payout_address})["result"]

    assert res["enabled"] is False
    assert res["reason"] == "offline_no_outbound_peers"


def test_get_block_template_offline_override_enabled_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
):
    class _Snap:
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return dict(self._data)

    class _Svc:
        def status_snapshot(self):
            return _Snap({"peers_total": 0, "peers_outbound": 0})

        def sync_status_snapshot(self):
            return _Snap(
                {
                    "head_height": 5,
                    "best_header_height": 5,
                    "best_block_height": 5,
                    "fatal_error": None,
                }
            )

    import p2p

    monkeypatch.delenv("ANIMICA_ALLOW_OFFLINE_MINING_FOR_TESTS", raising=False)
    monkeypatch.setenv("ANIMICA_MINING_MIN_PEERS", "0")
    monkeypatch.setattr(p2p, "get_service", lambda: _Svc())

    client, _, _ = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    blocked = rpc_call(client, "miner.getBlockTemplate", {"address": payout_address})["result"]
    assert blocked["enabled"] is False
    assert blocked["reason"] == "offline_no_outbound_peers"

    monkeypatch.setenv("ANIMICA_ALLOW_OFFLINE_MINING_FOR_TESTS", "1")
    enabled = rpc_call(client, "miner.getBlockTemplate", {"address": payout_address})["result"]
    assert enabled["enabled"] is True
    assert "header" in enabled


def test_offline_override_denied_on_mainnet(monkeypatch: pytest.MonkeyPatch):
    class _Cfg:
        chain_id = 1

    class _Ctx:
        cfg = _Cfg()

    monkeypatch.setenv("ANIMICA_ALLOW_OFFLINE_MINING_FOR_TESTS", "1")
    monkeypatch.setattr(miner_methods, "_ctx", lambda: _Ctx())

    assert (
        miner_methods._resolve_allow_offline_mining(
            True, source="test_offline_override_denied_on_mainnet"
        )
        is False
    )


@pytest.mark.asyncio
async def test_dispatch_accepts_empty_param_array():
    from rpc import jsonrpc

    payload = {"jsonrpc": "2.0", "id": 9, "method": "miner.getWork", "params": []}
    ctx = jsonrpc._default_ctx()

    res = await jsonrpc.dispatch(payload, ctx)

    assert res["result"]["jobId"] in miner_methods._JOB_CACHE


def test_get_sha256_job_shape():
    client, _, _ = new_test_client()
    miner_methods._LOCAL_HEAD.clear()
    res = rpc_call(client, "miner.get_sha256_job")
    job = res["result"]
    assert "prevhash" in job and len(job["prevhash"]) == 64
    assert "coinb1" in job and "coinb2" in job
    assert job["version"].startswith("2")
    assert job["nbits"]
    assert job["ntime"]
    assert job["clean_jobs"] is True


def test_submit_sha256_block_stub_accepts_payload():
    client, _, _ = new_test_client()
    payload = {"header": "deadbeef", "nonce": "01"}
    res = rpc_call(client, "miner.submit_sha256_block", payload)
    assert res["result"]["accepted"] is True
    assert res["result"]["payload"] == payload


def test_miner_mine_advances_head():
    client, cfg, _ = new_test_client()
    start = rpc_call(client, "chain.getHead")["result"].get("height") or 0

    mined = rpc_call(client, "miner.mine", [2])["result"]
    assert mined["mined"] == 2
    assert mined["height"] >= start + 2

    after = rpc_call(client, "chain.getHead")["result"].get("height") or 0
    assert after >= start + 2


def test_miner_mine_with_zero_transactions():
    """
    Test that mining a payout-only block (no pending transactions) succeeds.
    
    This test ensures no UnboundLocalError is thrown when mining with zero txs.
    Regression test for PR #426 fix.
    """
    client, cfg, _ = new_test_client()
    
    # Ensure pending pool is empty (no transactions to include)
    try:
        from rpc.methods import tx as tx_methods
        # Clear any pending transactions
        if hasattr(tx_methods, "_FALLBACK_PENDING"):
            tx_methods._FALLBACK_PENDING.clear()
        if hasattr(tx_methods, "_FALLBACK_PENDING_TS"):
            tx_methods._FALLBACK_PENDING_TS.clear()
    except (ImportError, AttributeError):
        # If modules/attributes not available, continue anyway
        pass
    
    # Mine a single block with no pending transactions
    start_height = rpc_call(client, "chain.getHead")["result"].get("height") or 0
    result = rpc_call(client, "miner.mine", [1])["result"]
    
    # Verify mining succeeded
    assert result["mined"] == 1
    assert result["height"] >= start_height + 1
    assert "totalReward" in result
    assert "rewards" in result
    assert len(result["rewards"]) == 1


def test_get_block_template_with_mempool_enabled(monkeypatch: pytest.MonkeyPatch):
    """
    Regression test for NameError when mempool_service is referenced.
    
    Verifies that miner.getBlockTemplate with include_mempool=True does not
    raise NameError when accessing mempool_service variable.
    """
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    client, _, _ = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    res = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": payout_address, "include_mempool": True},
    )

    assert res["result"] is not None
    assert "templateId" in res["result"]
    assert "header" in res["result"]
    assert "txs" in res["result"]
    assert res["result"]["coinbase"]["address"] == payout_address


def test_get_block_template_with_mempool_disabled(monkeypatch: pytest.MonkeyPatch):
    """
    Test that miner.getBlockTemplate works with include_mempool=False.
    """
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    client, _, _ = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    res = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": payout_address, "include_mempool": False},
    )

    assert res["result"] is not None
    assert "templateId" in res["result"]
    assert "header" in res["result"]


def test_get_block_template_scales_selection_to_pending_count(
    monkeypatch: pytest.MonkeyPatch,
):
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    class _Cfg:
        chain_id = 1337
        genesis_path = None

    parent_header = Header(
        v=1,
        chainId=1337,
        height=1,
        parentHash=miner_methods.ZERO32,
        timestamp=1_700_000_000,
        stateRoot=miner_methods.ZERO32,
        txsRoot=miner_methods.ZERO32,
        receiptsRoot=miner_methods.ZERO32,
        proofsRoot=miner_methods.ZERO32,
        daRoot=miner_methods.ZERO32,
        mixSeed=miner_methods.ZERO32,
        poiesPolicyRoot=miner_methods.ZERO32,
        pqAlgPolicyRoot=miner_methods.ZERO32,
        thetaMicro=1_000_000,
        nonce=0,
        extra=b"",
    )

    class _Ctx:
        cfg = _Cfg()
        params = {}
        state_db = None
        tx_index = None

    class _Adapter:
        def get_head(self):
            return {
                "height": 1,
                "hash": "0x" + ("11" * 32),
                "obj": parent_header,
            }

    class _Mempool:
        def stats(self):
            return {"count": 1500}

    captured: dict[str, int] = {}

    def _fake_collect(*, ctx, adapter, limit: int = 1000):
        captured["collect_limit"] = int(limit)
        entries = [
            miner_methods.PendingTxEntry(
                hash_hex=f"0x{i:064x}",
                raw=b"",
                tx=None,
            )
            for i in range(1500)
        ]
        return entries, {}, 1500

    class _Selection:
        def __init__(self, total_pending: int):
            self.selected = []
            self.selected_hashes = []
            self.rejected = {}
            self.rejected_by_hash = {}
            self.rejected_details_by_hash = {}
            self.total_pending = total_pending

    def _fake_select_for_block(*, limits, pending, **_kwargs):
        captured["max_txs"] = int(limits.get("max_txs") or 0)
        pending_count = len(pending) if isinstance(pending, list) else sum(1 for _ in pending)
        return _Selection(total_pending=pending_count)

    monkeypatch.setattr(miner_methods, "_ctx", lambda: _Ctx())
    monkeypatch.setattr(miner_methods, "_adapter", lambda: _Adapter())
    monkeypatch.setattr(miner_methods, "_mining_gate", lambda **_kwargs: (True, None))
    monkeypatch.setattr(miner_methods, "_min_block_spacing_s", lambda: 0.0)
    monkeypatch.setattr(
        miner_methods,
        "_current_head_snapshot",
        lambda: {
            "height": 1,
            "hash": "0x" + ("11" * 32),
            "header": parent_header,
        },
    )
    monkeypatch.setattr(miner_methods, "_resolve_mempool_service", lambda _ctx: _Mempool())
    monkeypatch.setattr(miner_methods, "_collect_mempool_entries", _fake_collect)
    monkeypatch.setattr(miner_methods, "select_for_block", _fake_select_for_block)

    res = miner_methods.miner_get_block_template(
        address=payout_address,
        include_mempool=True,
        sync_peer_mempools=False,
    )

    assert captured["collect_limit"] >= 1500
    assert captured["max_txs"] >= 1500
    assert int(res["mempool"]["pending"]) == 1500


def test_get_block_template_caps_selection_to_150k(
    monkeypatch: pytest.MonkeyPatch,
):
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    class _Cfg:
        chain_id = 1337
        genesis_path = None

    parent_header = Header(
        v=1,
        chainId=1337,
        height=1,
        parentHash=miner_methods.ZERO32,
        timestamp=1_700_000_000,
        stateRoot=miner_methods.ZERO32,
        txsRoot=miner_methods.ZERO32,
        receiptsRoot=miner_methods.ZERO32,
        proofsRoot=miner_methods.ZERO32,
        daRoot=miner_methods.ZERO32,
        mixSeed=miner_methods.ZERO32,
        poiesPolicyRoot=miner_methods.ZERO32,
        pqAlgPolicyRoot=miner_methods.ZERO32,
        thetaMicro=1_000_000,
        nonce=0,
        extra=b"",
    )

    class _Ctx:
        cfg = _Cfg()
        params = {}
        state_db = None
        tx_index = None

    class _Adapter:
        def get_head(self):
            return {
                "height": 1,
                "hash": "0x" + ("11" * 32),
                "obj": parent_header,
            }

    class _Mempool:
        def stats(self):
            return {"count": 200_000}

    captured: dict[str, int] = {}

    def _fake_collect(*, ctx, adapter, limit: int = 1000):
        captured["collect_limit"] = int(limit)
        entries = [
            miner_methods.PendingTxEntry(
                hash_hex=f"0x{i:064x}",
                raw=b"",
                tx=None,
            )
            for i in range(16)
        ]
        return entries, {}, 200_000

    class _Selection:
        def __init__(self, total_pending: int):
            self.selected = []
            self.selected_hashes = []
            self.rejected = {}
            self.rejected_by_hash = {}
            self.rejected_details_by_hash = {}
            self.total_pending = total_pending

    def _fake_select_for_block(*, limits, pending, **_kwargs):
        captured["max_txs"] = int(limits.get("max_txs") or 0)
        pending_count = len(pending) if isinstance(pending, list) else sum(1 for _ in pending)
        return _Selection(total_pending=pending_count)

    monkeypatch.setattr(miner_methods, "_ctx", lambda: _Ctx())
    monkeypatch.setattr(miner_methods, "_adapter", lambda: _Adapter())
    monkeypatch.setattr(miner_methods, "_mining_gate", lambda **_kwargs: (True, None))
    monkeypatch.setattr(miner_methods, "_min_block_spacing_s", lambda: 0.0)
    monkeypatch.setattr(
        miner_methods,
        "_current_head_snapshot",
        lambda: {
            "height": 1,
            "hash": "0x" + ("11" * 32),
            "header": parent_header,
        },
    )
    monkeypatch.setattr(miner_methods, "_resolve_mempool_service", lambda _ctx: _Mempool())
    monkeypatch.setattr(miner_methods, "_collect_mempool_entries", _fake_collect)
    monkeypatch.setattr(miner_methods, "select_for_block", _fake_select_for_block)

    res = miner_methods.miner_get_block_template(
        address=payout_address,
        include_mempool=True,
        sync_peer_mempools=False,
    )

    assert captured["collect_limit"] == 150_000
    assert captured["max_txs"] == 150_000
    assert int(res["mempool"]["pending"]) == 16
