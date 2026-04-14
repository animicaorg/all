from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION
from core.types.header import Header, serialize_header
from core.utils.hash import sha3_256
from rpc import errors as rpc_errors
from pq.py.address import decode_address
from rpc.tests import new_test_client, rpc_call
from rpc.methods import miner as miner_methods


def _parse_balance(result: dict) -> int:
    balance = result.get("result", 0)
    if isinstance(balance, str):
        return int(balance, 16) if balance.startswith("0x") else int(balance)
    return int(balance)


def _premine_address_hex() -> str:
    premine_addr_bech32 = MAINNET_PREMINE_DISTRIBUTION[0][0]
    addr_record = decode_address(premine_addr_bech32)
    digest = bytes(addr_record.digest) if isinstance(addr_record.digest, list) else addr_record.digest
    premine_addr_bytes = digest[:32].ljust(32, b"\x00")
    return "0x" + premine_addr_bytes.hex()


def _parse_hex_bytes(value: str) -> bytes:
    hex_value = value[2:] if value.startswith("0x") else value
    if len(hex_value) % 2:
        hex_value = "0" + hex_value
    return bytes.fromhex(hex_value)


def _header_from_template(header_view: dict) -> Header:
    return Header(
        v=int(header_view.get("v", 1)),
        chainId=int(header_view.get("chainId", header_view.get("chain_id", 0))),
        height=int(header_view.get("height", header_view.get("number", 0))),
        parentHash=_parse_hex_bytes(header_view["parentHash"]),
        timestamp=int(header_view.get("timestamp", 0)),
        stateRoot=_parse_hex_bytes(header_view.get("stateRoot", "0x" + "00" * 32)),
        txsRoot=_parse_hex_bytes(header_view.get("txsRoot", "0x" + "00" * 32)),
        receiptsRoot=_parse_hex_bytes(header_view.get("receiptsRoot", "0x" + "00" * 32)),
        proofsRoot=_parse_hex_bytes(header_view.get("proofsRoot", "0x" + "00" * 32)),
        daRoot=_parse_hex_bytes(header_view.get("daRoot", "0x" + "00" * 32)),
        mixSeed=_parse_hex_bytes(header_view.get("mixSeed", "0x" + "00" * 32)),
        poiesPolicyRoot=_parse_hex_bytes(
            header_view.get("poiesPolicyRoot", "0x" + "00" * 32)
        ),
        pqAlgPolicyRoot=_parse_hex_bytes(
            header_view.get("pqAlgPolicyRoot", "0x" + "00" * 32)
        ),
        thetaMicro=int(header_view.get("thetaMicro", 0)),
        workType=int(header_view.get("workType", 0)),
        nonce=int(header_view.get("nonce", 0)),
        extra=_parse_hex_bytes(header_view.get("extra", "0x")),
    )


def _find_nonce(header: Header, target_int: int, max_nonce: int = 10000) -> tuple[int, bytes]:
    for nonce in range(max_nonce):
        candidate = replace(header, nonce=nonce)
        digest = sha3_256(serialize_header(candidate))
        if int.from_bytes(digest, "big") <= target_int:
            return nonce, digest
    pytest.skip("could not find valid nonce within search space")


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


def test_submit_block_accepts_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    monkeypatch.setenv("ANIMICA_DEFAULT_THETA_MICRO", "1000")
    monkeypatch.setenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0")
    monkeypatch.setenv("ANIMICA_MINER_MAX_NONCE", "50000")

    client, _cfg, _tmp = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    head_before = rpc_call(client, "chain.getHead")["result"]
    balance_before = _parse_balance(
        rpc_call(client, "state.getBalance", [_premine_address_hex()])
    )

    template = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": payout_address, "include_mempool": False},
    )["result"]

    header = _header_from_template(template["header"])
    target_int = int(template["target"], 16)
    nonce, _digest = _find_nonce(header, target_int)
    header = replace(header, nonce=nonce)

    header_payload = {
        k: ("0x" + v.hex() if isinstance(v, (bytes, bytearray)) else v)
        for k, v in header.to_obj().items()
    }
    txs_raw = [tx.get("raw") for tx in template.get("txs", []) if isinstance(tx, dict)]
    block_payload = {
        "header": header_payload,
        "txs": txs_raw,
        "proofs": [],
        "parentHash": template["parent"]["hash"],
        "templateId": template.get("templateId"),
    }

    submit = rpc_call(client, "miner.submitBlock", block_payload)["result"]
    assert submit["accepted"] is True

    head_after = rpc_call(client, "chain.getHead")["result"]
    assert int(head_after.get("height", 0)) == int(head_before.get("height", 0)) + 1

    balance_after = _parse_balance(
        rpc_call(client, "state.getBalance", [_premine_address_hex()])
    )
    assert balance_after >= balance_before


def test_submit_block_rejects_stale_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    monkeypatch.setenv("ANIMICA_DEFAULT_THETA_MICRO", "1000")
    monkeypatch.setenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0")
    monkeypatch.setenv("ANIMICA_MINER_MAX_NONCE", "50000")

    client, _cfg, _tmp = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    template = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": payout_address, "include_mempool": False},
    )["result"]

    header = _header_from_template(template["header"])
    target_int = int(template["target"], 16)
    nonce, _digest = _find_nonce(header, target_int)
    header = replace(header, nonce=nonce)

    rpc_call(client, "miner.mine", {"count": 1, "address": payout_address})

    header_payload = {
        k: ("0x" + v.hex() if isinstance(v, (bytes, bytearray)) else v)
        for k, v in header.to_obj().items()
    }
    txs_raw = [tx.get("raw") for tx in template.get("txs", []) if isinstance(tx, dict)]
    block_payload = {
        "header": header_payload,
        "txs": txs_raw,
        "proofs": [],
        "parentHash": template["parent"]["hash"],
        "templateId": template.get("templateId"),
    }

    error = rpc_call(client, "miner.submitBlock", block_payload, expect_error=True)[
        "error"
    ]
    assert error["code"] == -32063
    assert error["message"] == "stale template"
    assert error["data"]["reason"] == "stale_template"


def test_template_includes_lease_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    client, _cfg, _tmp = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    template = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": payout_address, "include_mempool": False, "ttlSeconds": 12},
    )["result"]

    assert template.get("templateId")
    assert template.get("issuedAt") is not None
    assert template.get("expiresAt") is not None
    assert template.get("headHashAtIssue") is not None
    assert int(template["expiresAt"]) >= int(template["issuedAt"])


def test_submit_block_rejects_expired_template(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANIMICA_MINING_FORCE", "1")
    monkeypatch.setenv("ANIMICA_DEFAULT_THETA_MICRO", "1000")
    monkeypatch.setenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0")
    monkeypatch.setenv("ANIMICA_MINER_MAX_NONCE", "50000")

    client, _cfg, _tmp = new_test_client()
    payout_address = MAINNET_PREMINE_DISTRIBUTION[0][0]

    template = rpc_call(
        client,
        "miner.getBlockTemplate",
        {"address": payout_address, "include_mempool": False, "ttlSeconds": 5},
    )["result"]

    header = _header_from_template(template["header"])
    target_int = int(template["target"], 16)
    nonce, _digest = _find_nonce(header, target_int)
    header = replace(header, nonce=nonce)

    # Force cache entry expiry deterministically (no real sleep needed).
    tid = str(template.get("templateId"))
    assert tid in miner_methods._TEMPLATE_CACHE
    miner_methods._TEMPLATE_CACHE[tid]["expires_at"] = 0

    header_payload = {
        k: ("0x" + v.hex() if isinstance(v, (bytes, bytearray)) else v)
        for k, v in header.to_obj().items()
    }
    txs_raw = [tx.get("raw") for tx in template.get("txs", []) if isinstance(tx, dict)]
    block_payload = {
        "header": header_payload,
        "txs": txs_raw,
        "proofs": [],
        "parentHash": template["parent"]["hash"],
        "templateId": template.get("templateId"),
    }

    error = rpc_call(client, "miner.submitBlock", block_payload, expect_error=True)["error"]
    assert error["code"] == -32063
    assert error["data"]["reason"] == "stale_template"
    assert error["data"].get("detail") == "template_expired"


def test_submit_block_payload_extraction_compatibility() -> None:
    block_payload = {
        "header": {"height": 1, "parentHash": "0x" + "00" * 32},
        "txs": [],
        "proofs": [],
        "parentHash": "0x" + "00" * 32,
    }

    # Positional payload (CLI path)
    extracted = miner_methods._extract_payload(block_payload, {})
    assert extracted == block_payload

    # Wrapped list payload (legacy/stratum variants)
    extracted = miner_methods._extract_payload([block_payload], {})
    assert extracted == block_payload

    # Keyword-only payload (regression guard for unexpected keyword argument paths)
    extracted = miner_methods._extract_payload(None, dict(block_payload))
    assert extracted == block_payload


def test_submit_work_happy_path_with_stubbed_submit_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mining.templates import HeaderTemplate, MiningJob

    snapshot = _snapshot_miner_globals()
    try:
        header_tpl = HeaderTemplate(
            parent_hash=b"\x11" * 32,
            number=5,
            chain_id=1,
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
            job_id="job-submit-work",
            parent_hash=header_tpl.parent_hash,
            parent_height=4,
            chain_id=1,
            target=(1 << 256) - 1,
            theta_target_micro=header_tpl.theta_target_micro,
            proof_type="sha256d",
            challenge=None,
            expires_at=None,
            template_version=1,
            header=header_tpl,
            sign_bytes=header_tpl.to_sign_bytes(),
        )

        miner_methods._JOB_CACHE.clear()
        miner_methods._JOB_CACHE[job_obj.job_id] = {
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

        calls = {"head": 0}

        def _fake_head_snapshot():
            calls["head"] += 1
            if calls["head"] == 1:
                return {"height": 4, "hash": "0x" + ("11" * 32), "generation": 7}
            return {"height": 5, "hash": "0x" + ("ab" * 32), "generation": 8}

        def _stub_submit_block(_payload: dict[str, Any]) -> dict[str, Any]:
            return {
                "accepted": True,
                "duplicate": False,
                "newHead": {"height": 5, "hash": "0x" + ("ab" * 32)},
                "new_head": 5,
                "new_head_hash": "0x" + ("ab" * 32),
                "block_hash": "0x" + ("ab" * 32),
                "expected_reward": 100,
                "credited_amount": 100,
                "credited_delta": 100,
                "credited_source": "state_balance_delta",
                "balance_before": 1_000,
                "balance_now": 1_100,
            }

        monkeypatch.setattr(miner_methods, "_current_head_snapshot", _fake_head_snapshot)
        monkeypatch.setattr(miner_methods, "miner_submit_block", _stub_submit_block)

        result = miner_methods.miner_submit_work(jobId=job_obj.job_id, nonce="0x2a")

        assert result["accepted"] is True
        assert result["reason"] is None
        assert result["newHead"]["hash"] == "0x" + ("ab" * 32)
        assert result["expected_reward"] == 100
        assert result["credited_amount"] == 100
    finally:
        _restore_miner_globals(snapshot)


def test_submit_block_invalid_payload_is_invalid_params_not_nameerror() -> None:
    with pytest.raises(rpc_errors.InvalidParams, match="parent_hash or template_id is required"):
        miner_methods.miner_submit_block({"header": {}})


def test_submit_work_returns_structured_internal_reason_for_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mining.templates import HeaderTemplate, MiningJob

    snapshot = _snapshot_miner_globals()
    try:
        header_tpl = HeaderTemplate(
            parent_hash=b"\x33" * 32,
            number=9,
            chain_id=1,
            state_root=b"\x00" * 32,
            txs_root=b"\x00" * 32,
            receipts_root=b"\x00" * 32,
            proofs_root=b"\x00" * 32,
            da_root=b"\x00" * 32,
            theta_target_micro=1_000_000,
            mix_seed=b"\x44" * 32,
            pq_alg_policy_root=b"\x00" * 32,
            poies_policy_root=b"\x00" * 32,
            timestamp=1_700_000_000,
            work_type=0,
            extra=b"",
        )
        job_obj = MiningJob(
            job_id="job-submit-work-error",
            parent_hash=header_tpl.parent_hash,
            parent_height=8,
            chain_id=1,
            target=(1 << 256) - 1,
            theta_target_micro=header_tpl.theta_target_micro,
            proof_type="sha256d",
            challenge=None,
            expires_at=None,
            template_version=1,
            header=header_tpl,
            sign_bytes=header_tpl.to_sign_bytes(),
        )

        miner_methods._JOB_CACHE.clear()
        miner_methods._JOB_CACHE[job_obj.job_id] = {
            "job": job_obj,
            "sign_bytes": job_obj.sign_bytes,
            "mix_seed": job_obj.header.mix_seed,
            "block_target": (1 << 256) - 1,
            "height": int(job_obj.header.number),
            "created_at": 0.0,
            "parent_hash": job_obj.parent_hash,
            "parent_height": job_obj.parent_height,
            "chain_id": int(job_obj.chain_id),
            "head_generation": 3,
        }

        monkeypatch.setattr(
            miner_methods,
            "_current_head_snapshot",
            lambda: {"height": 8, "hash": "0x" + ("33" * 32), "generation": 3},
        )

        def _boom(*_args, **_kwargs):
            raise NameError("name '_extract_payload' is not defined")

        monkeypatch.setattr(miner_methods, "miner_submit_block", _boom)

        with pytest.raises(rpc_errors.RpcError) as exc_info:
            miner_methods.miner_submit_work(jobId=job_obj.job_id, nonce="0x1")

        err = exc_info.value
        assert err.code == -32000
        assert err.message == "submitWork failed"
        assert err.data["reason"] == "submit_work_failed"
        assert "NameError" in err.data["detail"]
    finally:
        _restore_miner_globals(snapshot)
