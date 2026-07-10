"""Tests for animica.ena.inference_receipt — Proof-of-Inference (7.1.1 P1)."""

import pytest

from animica.ena import inference_receipt as IR


def _key():
    from pq.py.keygen import keygen_sig
    return keygen_sig("ml_dsa_65")


def test_hash_recompute_stability():
    # A receipt carries a unique id + timestamp, so two independent builds differ
    # (they are different receipts). The invariant that matters is that anyone can
    # recompute the SAME hash from a given receipt's content — offline.
    r = IR.build_inference_receipt(model_id="m", provider_key="deterministic", prompt="p",
                                   output="o", tokens_in=1, tokens_out=1, nonce_hex="ab" * 16)
    d = r.to_dict()
    assert IR.compute_inference_hash(d) == r.receipt_hash
    # recompute is stable across copies and ignores volatile/signature fields
    d2 = dict(d)
    d2["signature_hex"] = "ff" * 100
    d2["anchor_txid"] = "0xabc"
    assert IR.compute_inference_hash(d2) == r.receipt_hash


def test_sign_validate_round_trip():
    k = _key()
    r = IR.build_inference_receipt(model_id="deterministic", provider_key="deterministic",
                                   prompt="hi", output="ok", tokens_in=1, tokens_out=1)
    r = IR.sign_inference_receipt(r, key=k)
    assert r.signed is True
    v = IR.validate_inference_receipt(r.to_dict())
    assert v["valid"] and v["hash_ok"] and v["signature_ok"] and v["signed"]


def test_content_tamper_breaks_hash():
    k = _key()
    r = IR.sign_inference_receipt(
        IR.build_inference_receipt(model_id="m", provider_key="deterministic", prompt="p",
                                   output="o", tokens_in=1, tokens_out=1), key=k)
    d = dict(r.to_dict())
    d["output_hash"] = "de" * 32
    v = IR.validate_inference_receipt(d)
    assert v["valid"] is False and v["hash_ok"] is False


def test_signature_tamper_and_wrong_domain_fail_closed():
    k = _key()
    r = IR.sign_inference_receipt(
        IR.build_inference_receipt(model_id="m", provider_key="deterministic", prompt="p",
                                   output="o", tokens_in=1, tokens_out=1), key=k)
    d = dict(r.to_dict())
    # flip a signature byte → signature_ok False, hash still ok, no exception raised
    d["signature_hex"] = ("00" if d["signature_hex"][:2] != "00" else "11") + d["signature_hex"][2:]
    v = IR.validate_inference_receipt(d)
    assert v["hash_ok"] is True and v["signature_ok"] is False and v["valid"] is False

    d2 = dict(r.to_dict())
    d2["signature_domain"] = "some.other.domain.v1"
    assert IR.validate_inference_receipt(d2)["signature_ok"] is False


def test_spoofed_signer_address_is_rejected():
    # A genuine signature + pubkey (from key A) but a signer_address relabeled to a
    # different identity must NOT validate — the address is bound to the pubkey.
    k = _key()
    r = IR.sign_inference_receipt(
        IR.build_inference_receipt(model_id="m", provider_key="deterministic", prompt="p",
                                   output="o", tokens_in=1, tokens_out=1), key=k)
    d = dict(r.to_dict())
    # a real, different address (derived from a second, unrelated key)
    other = _key().address
    assert other != d["signer_address"]
    d["signer_address"] = other
    v = IR.validate_inference_receipt(d)
    assert v["hash_ok"] is True and v["signature_ok"] is False and v["valid"] is False


def test_swapped_pubkey_is_rejected():
    # Swapping in an attacker's pubkey (whose address they could also set) must fail:
    # the signature no longer verifies under that key.
    k = _key()
    r = IR.sign_inference_receipt(
        IR.build_inference_receipt(model_id="m", provider_key="deterministic", prompt="p",
                                   output="o", tokens_in=1, tokens_out=1), key=k)
    attacker = _key()
    d = dict(r.to_dict())
    d["signer_pubkey_hex"] = attacker.public_key.hex()
    d["signer_address"] = attacker.address
    assert IR.validate_inference_receipt(d)["signature_ok"] is False


def test_fail_open_unsigned_but_valid(monkeypatch):
    monkeypatch.setenv("ANIMICA_PQ_MODE", "disabled")
    from animica.ai import nodekey
    nodekey.reset_cache()
    r = IR.sign_inference_receipt(
        IR.build_inference_receipt(model_id="m", provider_key="deterministic", prompt="p",
                                   output="o", tokens_in=1, tokens_out=1))
    assert r.signed is False
    v = IR.validate_inference_receipt(r.to_dict())
    assert v["valid"] is True and v["hash_ok"] is True and v["signed"] is False
    nodekey.reset_cache()


def test_quantum_seed_provenance_recorded():
    r = IR.build_inference_receipt(
        model_id="m", provider_key="deterministic", prompt="p", output="o",
        tokens_in=1, tokens_out=1,
        qseed={"seed_hex": "aa" * 32, "seed_applied": True, "beacon_round": 5,
               "attested": True, "attestation_backend": "hardware"})
    assert r.seed_applied is True and r.beacon_round == 5
    assert r.attested is True and r.attestation_backend == "hardware"


def test_export_envelope():
    k = _key()
    r = IR.sign_inference_receipt(
        IR.build_inference_receipt(model_id="m", provider_key="deterministic", prompt="p",
                                   output="o", tokens_in=1, tokens_out=1), key=k)
    env = IR.build_inference_export_envelope(r.to_dict())
    assert env["schema"] == IR.EXPORT_SCHEMA
    assert env["validation"]["valid"] is True
    assert env["onchain"]["receipt_hash"] == r.receipt_hash
