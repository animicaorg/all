from dataclasses import replace

from core.types.header import serialize_header
from core.utils.hash import sha3_256
from mining.template_block import (build_submit_block_payload,
                                   hash_candidate_header,
                                   header_from_template_view)


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


def test_hash_candidate_header_matches_canonical_header_hash() -> None:
    header_view = _full_header_template()
    header = header_from_template_view(header_view)
    nonce = 9

    candidate = hash_candidate_header(header_view, nonce=nonce)
    manual = sha3_256(serialize_header(replace(header, nonce=nonce)))

    assert candidate.header.nonce == nonce
    assert candidate.digest == manual


def test_build_submit_block_payload_preserves_template_metadata_and_txs() -> None:
    template = {
        "templateId": "template-7",
        "header": _full_header_template(),
        "target": "0x" + "ff" * 32,
        "parent": {"height": 6, "hash": "0x" + "aa" * 32},
        "txs": [{"hash": "0x01", "raw": "0xcafe"}, "0xbeef"],
        "proofs": [{"kind": "noop"}],
    }

    payload = build_submit_block_payload(template, nonce=5)

    assert payload["templateId"] == "template-7"
    assert payload["parentHash"] == "0x" + "aa" * 32
    assert payload["header"]["nonce"] == 5
    assert payload["txs"] == ["0xcafe", "0xbeef"]
    assert payload["proofs"] == [{"kind": "noop"}]
