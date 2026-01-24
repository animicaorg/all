from __future__ import annotations

import pytest

from p2p.protocol import build_hello_caps
from p2p.protocol import hello as hello_mod


try:
    from pq.py.keygen import keygen_sig
except Exception as exc:  # pragma: no cover - optional PQ backend
    pytest.skip(f"PQ keygen unavailable: {exc}", allow_module_level=True)


def _build_test_hello() -> tuple[bytes, dict[str, object]]:
    keypair = keygen_sig("dilithium3")
    transcript_hash = b"\x22" * 32
    alg_policy_root = b"\x11" * 64
    caps = build_hello_caps(chain_id=1)
    payload = hello_mod.build_hello_message(
        alg_id=keypair.alg_id,
        public_key=keypair.public_key,
        sign_key=keypair.secret_key,
        chain_id=1,
        fork_id=123,
        consensus_id="consensus-v1",
        protocol_version="1.0",
        alg_policy_root=alg_policy_root,
        caps=caps,
        transcript_hash=transcript_hash,
    )
    expectations = {
        "expected_chain_id": 1,
        "expected_fork_id": 123,
        "expected_consensus_id": "consensus-v1",
        "expected_protocol_version": "1.0",
        "expected_transcript_hash": transcript_hash,
        "expected_alg_policy_root": alg_policy_root,
        "alg_id": keypair.alg_id,
    }
    return payload, expectations


def test_hello_roundtrip_verify() -> None:
    payload, expectations = _build_test_hello()
    verified = hello_mod.verify_hello_message(
        payload,
        expected_chain_id=expectations["expected_chain_id"],
        expected_fork_id=expectations["expected_fork_id"],
        expected_consensus_id=expectations["expected_consensus_id"],
        expected_protocol_version=expectations["expected_protocol_version"],
        expected_transcript_hash=expectations["expected_transcript_hash"],
        expected_alg_policy_root=expectations["expected_alg_policy_root"],
    )
    assert verified.chain_id == expectations["expected_chain_id"]
    assert verified.fork_id == expectations["expected_fork_id"]
    assert verified.protocol_version == expectations["expected_protocol_version"]
    assert verified.alg_id == expectations["alg_id"]


def test_hello_rejects_version_mismatch() -> None:
    payload, expectations = _build_test_hello()
    hello = hello_mod._decoder.decode(payload)
    bad = hello_mod._HelloStruct(**{**hello.__dict__, "vmaj": hello.vmaj + 1})
    bad_payload = hello_mod._encoder.encode(bad)
    with pytest.raises(hello_mod.ProtocolError, match="protocol major mismatch"):
        hello_mod.verify_hello_message(
            bad_payload,
            expected_chain_id=expectations["expected_chain_id"],
            expected_fork_id=expectations["expected_fork_id"],
            expected_consensus_id=expectations["expected_consensus_id"],
            expected_protocol_version=expectations["expected_protocol_version"],
            expected_transcript_hash=expectations["expected_transcript_hash"],
            expected_alg_policy_root=expectations["expected_alg_policy_root"],
        )

