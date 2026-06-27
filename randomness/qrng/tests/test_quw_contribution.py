"""End-to-end tests for the Quantum Useful Work contribution lane (software path)."""

from __future__ import annotations

import os

from randomness.qrng import providers, hsm_tpm, contribution as quw


def _source():
    # Health-gated software fallback: uniform CSPRNG passes the SP800-90B gate.
    return providers.HealthGatedSource(providers.SoftwareFallbackQRNG())


def test_build_verify_roundtrip_software():
    src = _source()
    signer = hsm_tpm.SoftwareSelfSigner(key_dir="/tmp/claude-0/quw-test-keys")
    nonce = os.urandom(16)
    c = quw.build_contribution(src, signer, round_id=7, nonce=nonce,
                               address="anim1quwtest", n_bytes=4096)
    seen: set = set()
    vr = quw.verify_contribution(c, expected_nonce=nonce, seen_nullifiers=seen)
    assert vr.verified, vr.reason
    assert vr.health_passed
    assert vr.attested is False  # software signer is non-attested
    assert vr.min_entropy_per_byte >= 7.0
    # reward metrics are present but discounted (software, non-attested)
    m = quw.contribution_to_quantum_metrics(c, vr)
    assert m["quantum_units"] > 0.0
    assert m["traps_ratio"] == 1.0
    assert m["qos"] < 0.5  # discounted


def test_replay_rejected():
    src = _source()
    signer = hsm_tpm.SoftwareSelfSigner(key_dir="/tmp/claude-0/quw-test-keys")
    nonce = os.urandom(16)
    c = quw.build_contribution(src, signer, round_id=1, nonce=nonce, address="a", n_bytes=2048)
    seen: set = set()
    assert quw.verify_contribution(c, expected_nonce=nonce, seen_nullifiers=seen).verified
    # second time -> replay
    vr2 = quw.verify_contribution(c, expected_nonce=nonce, seen_nullifiers=seen)
    assert not vr2.verified and "replay" in vr2.reason


def test_nonce_binding():
    src = _source()
    signer = hsm_tpm.SoftwareSelfSigner(key_dir="/tmp/claude-0/quw-test-keys")
    nonce = os.urandom(16)
    c = quw.build_contribution(src, signer, round_id=1, nonce=nonce, address="a", n_bytes=2048)
    vr = quw.verify_contribution(c, expected_nonce=os.urandom(16))  # wrong nonce
    assert not vr.verified and "nonce" in vr.reason


def test_tampered_entropy_rejected():
    src = _source()
    signer = hsm_tpm.SoftwareSelfSigner(key_dir="/tmp/claude-0/quw-test-keys")
    nonce = os.urandom(16)
    c = quw.build_contribution(src, signer, round_id=1, nonce=nonce, address="a", n_bytes=2048)
    # flip the entropy after signing -> embedded report n_samples still matches length,
    # but the signature is over the original report; tamper the *signature*'s subject by
    # swapping entropy for a different-length buffer to break the report match, and also
    # a same-length tamper to ensure signature still binds via report content.
    d = c.to_dict()
    d["entropy_hex"] = os.urandom(4096).hex()  # different length than 2048 -> report mismatch
    tampered = quw.QuantumContribution.from_dict(d)
    vr = quw.verify_contribution(tampered, expected_nonce=nonce)
    assert not vr.verified


def test_tampered_signature_rejected():
    src = _source()
    signer = hsm_tpm.SoftwareSelfSigner(key_dir="/tmp/claude-0/quw-test-keys")
    nonce = os.urandom(16)
    c = quw.build_contribution(src, signer, round_id=1, nonce=nonce, address="a", n_bytes=2048)
    d = c.to_dict()
    sig = bytearray(bytes.fromhex(d["signature_hex"]))
    sig[0] ^= 0xFF
    d["signature_hex"] = bytes(sig).hex()
    tampered = quw.QuantumContribution.from_dict(d)
    vr = quw.verify_contribution(tampered, expected_nonce=nonce)
    assert not vr.verified and "attestation" in vr.reason


def test_signature_verifies_with_cryptography():
    # ensure we are exercising real ed25519 (not the HMAC fallback) when crypto present
    signer = hsm_tpm.SoftwareSelfSigner(key_dir="/tmp/claude-0/quw-test-keys")
    assert signer.info().alg == hsm_tpm.ALG_ED25519
    msg = b"animica-quw-transcript"
    sig = signer.sign(msg)
    assert hsm_tpm.verify_signature(hsm_tpm.ALG_ED25519, signer.public_key(), msg, sig)
    assert not hsm_tpm.verify_signature(hsm_tpm.ALG_ED25519, signer.public_key(), b"other", sig)
