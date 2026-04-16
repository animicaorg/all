from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.types.header import Header, serialize_header
from core.utils.hash import sha3_256
from mining.pow_validation import derive_share_target_int
from rpc.methods import miner as miner_methods


def _full_header_template() -> dict:
    return {
        "v": 1,
        "chainId": 1,
        "height": 7,
        "parentHash": "0x" + "11" * 32,
        "timestamp": 1_800_000_000,
        "stateRoot": "0x" + "22" * 32,
        "txsRoot": "0x" + "33" * 32,
        "receiptsRoot": "0x" + "44" * 32,
        "proofsRoot": "0x" + "55" * 32,
        "daRoot": "0x" + "66" * 32,
        "mixSeed": "0x" + "77" * 32,
        "poiesPolicyRoot": "0x" + "88" * 32,
        "pqAlgPolicyRoot": "0x" + "99" * 32,
        "thetaMicro": 1_000_000,
        "workType": 0,
        "nonce": 0,
        "extra": "0x",
    }


def _header_obj(header_view: dict) -> Header:
    def _parse(value: str) -> bytes:
        return bytes.fromhex(value[2:])

    return Header(
        v=int(header_view.get("v", 1)),
        chainId=int(header_view.get("chainId", 0)),
        height=int(header_view.get("height") or header_view.get("number") or 0),
        parentHash=_parse(header_view["parentHash"]),
        timestamp=int(header_view["timestamp"]),
        stateRoot=_parse(header_view["stateRoot"]),
        txsRoot=_parse(header_view["txsRoot"]),
        receiptsRoot=_parse(header_view["receiptsRoot"]),
        proofsRoot=_parse(header_view["proofsRoot"]),
        daRoot=_parse(header_view["daRoot"]),
        mixSeed=_parse(header_view["mixSeed"]),
        poiesPolicyRoot=_parse(header_view["poiesPolicyRoot"]),
        pqAlgPolicyRoot=_parse(header_view["pqAlgPolicyRoot"]),
        thetaMicro=int(header_view["thetaMicro"]),
        workType=int(header_view.get("workType", 0)),
        nonce=int(header_view.get("nonce", 0)),
        extra=b"",
    )


def _find_nonce_for_target(header_view: dict, target_int: int, limit: int = 20_000) -> int:
    header = _header_obj(header_view)
    for nonce in range(limit):
        digest = sha3_256(serialize_header(replace(header, nonce=nonce)))
        if int.from_bytes(digest, "big", signed=False) <= target_int:
            return nonce
    raise AssertionError("unable to find nonce within search window")


def _find_nonce_between_targets(
    header_view: dict,
    *,
    upper_target: int,
    lower_exclusive: int,
    limit: int = 20_000,
) -> int:
    header = _header_obj(header_view)
    for nonce in range(limit):
        digest = sha3_256(serialize_header(replace(header, nonce=nonce)))
        digest_int = int.from_bytes(digest, "big", signed=False)
        if digest_int <= upper_target and digest_int > lower_exclusive:
            return nonce
    raise AssertionError("unable to find nonce between targets")


def _find_nonce_above_target(
    header_view: dict,
    *,
    target_int: int,
    limit: int = 20_000,
) -> int:
    header = _header_obj(header_view)
    for nonce in range(limit):
        digest = sha3_256(serialize_header(replace(header, nonce=nonce)))
        if int.from_bytes(digest, "big", signed=False) > target_int:
            return nonce
    raise AssertionError("unable to find nonce above target")


def test_miner_submit_share_is_deterministic_and_matches_block_target(monkeypatch: pytest.MonkeyPatch) -> None:
    job_cache_snapshot = dict(miner_methods._JOB_CACHE)
    try:
        header = _full_header_template()
        theta_micro = int(header["thetaMicro"])
        share_ratio = 0.6
        share_target_int = derive_share_target_int(theta_micro, share_ratio)
        block_target_int = share_target_int // 2

        nonce_share_only = _find_nonce_between_targets(
            header,
            upper_target=share_target_int,
            lower_exclusive=block_target_int,
        )
        nonce_block = _find_nonce_for_target(header, block_target_int)
        nonce_low = _find_nonce_above_target(header, target_int=share_target_int)

        parent_hash = bytes.fromhex(header["parentHash"][2:])
        job_id = "submit-share-target-parity"
        miner_methods._JOB_CACHE.clear()
        miner_methods._JOB_CACHE[job_id] = {
            "job": SimpleNamespace(theta_target_micro=theta_micro),
            "header_override": dict(header),
            "sign_bytes": b"\x00" * 32,  # intentionally unusable; canonical header path must win
            "mix_seed": bytes.fromhex(header["mixSeed"][2:]),
            "block_target": int(block_target_int),
            "share_target": float(share_ratio),
            "height": int(header["height"]),
            "created_at": 0.0,
            "parent_hash": parent_hash,
            "head_generation": 4242,
        }

        monkeypatch.setattr(
            miner_methods,
            "_current_head_snapshot",
            lambda: {"generation": 4242, "height": 6, "hash": header["parentHash"]},
        )
        monkeypatch.setattr(
            miner_methods,
            "_head_info",
            lambda: (parent_hash, 6, b"\x00" * 32, 1, b"\x00" * 32),
        )

        share_payload = {"jobId": job_id, "nonce": hex(nonce_share_only)}
        first = miner_methods.miner_submit_share(share_payload)
        second = miner_methods.miner_submit_share(share_payload)
        assert first == second
        assert first["accepted"] is True
        assert first["isBlock"] is False

        block = miner_methods.miner_submit_share({"jobId": job_id, "nonce": hex(nonce_block)})
        assert block["accepted"] is True
        assert block["isBlock"] is True

        low = miner_methods.miner_submit_share({"jobId": job_id, "nonce": hex(nonce_low)})
        assert low["accepted"] is False
        assert low["reason"] == "low difficulty share"
    finally:
        miner_methods._JOB_CACHE.clear()
        miner_methods._JOB_CACHE.update(job_cache_snapshot)

