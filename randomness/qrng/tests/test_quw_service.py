"""Tests for the QuantumWorkService coordinator (challenge/contribute/credits)."""

from __future__ import annotations

from randomness.qrng import providers, hsm_tpm, contribution as quw
from randomness.qrng.service import QuantumWorkService


def _contrib(svc, round_id, address, n=2048):
    ch = svc.get_challenge(round_id)
    src = providers.HealthGatedSource(providers.SoftwareFallbackQRNG())
    signer = hsm_tpm.SoftwareSelfSigner(key_dir="/tmp/claude-0/quw-test-keys")
    c = quw.build_contribution(src, signer, round_id=round_id,
                               nonce=bytes.fromhex(ch["nonce_hex"]),
                               address=address, n_bytes=n)
    return svc.contribute(c.to_dict())


def test_challenge_contribute_credit():
    svc = QuantumWorkService()
    r = _contrib(svc, 10, "anim1alice")
    assert r["accepted"], r
    assert r["mixed"] is True  # only contribution this round
    assert r["credited_units"] > 0
    cr = svc.get_credits("anim1alice")
    assert cr["total_units"] > 0
    st = svc.status()
    assert st["contributions_total"] == 1
    assert st["active_contributors"] == 1


def test_contribute_requires_challenge():
    svc = QuantumWorkService()
    # craft a contribution for a round with no challenge
    src = providers.HealthGatedSource(providers.SoftwareFallbackQRNG())
    signer = hsm_tpm.SoftwareSelfSigner(key_dir="/tmp/claude-0/quw-test-keys")
    import os
    c = quw.build_contribution(src, signer, round_id=999, nonce=os.urandom(32),
                               address="a", n_bytes=2048)
    r = svc.contribute(c.to_dict())
    assert not r["accepted"] and "challenge" in r["reason"]


def test_per_address_cap():
    svc = QuantumWorkService(per_round_per_address_cap=1)
    assert _contrib(svc, 5, "bob")["accepted"]
    r2 = _contrib(svc, 5, "bob")
    assert not r2["accepted"] and "cap" in r2["reason"]


def test_best_entropy_for_round_prefers_higher_entropy():
    svc = QuantumWorkService()
    _contrib(svc, 7, "alice")
    _contrib(svc, 7, "bob")
    best = svc.best_entropy_for_round(7)
    assert best is not None
    assert best["round_id"] == 7
    assert best["tag"] in ("mixed_with_attested_qrng", "mixed_with_unattested_qrng")
    assert len(bytes.fromhex(best["entropy_hex"])) > 0
