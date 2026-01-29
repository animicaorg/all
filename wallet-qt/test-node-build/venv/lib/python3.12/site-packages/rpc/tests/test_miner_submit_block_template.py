from __future__ import annotations

from dataclasses import replace

import pytest

from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION
from core.types.header import Header, serialize_header
from core.utils.hash import sha3_256
from pq.py.address import decode_address
from rpc.tests import new_test_client, rpc_call


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
