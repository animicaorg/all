from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Sequence

import pytest

from consensus import validator as validator_mod
from consensus.interfaces import (PROOF_TYPE_HASHSHARE, PROOF_TYPE_HASH_WORK,
                                  HeaderView, PolicySnapshot, ProofEnvelope,
                                  ProofMetrics, VerificationResult)
from consensus.types import ProofType
from consensus.work_registry import WorkAvailability


@dataclass
class _Policy:
    poies_policy_root: bytes
    alg_policy_root: bytes


@dataclass
class _Header:
    height: int
    chain_id: int
    theta_micro: int
    poies_policy_root: bytes
    policy_alg_root: bytes
    da_root: bytes = b"\x00" * 32
    proofs_root: bytes = b"\x00" * 32
    mix_seed: bytes = b"\x11" * 32
    work_type: int | None = None


class _FakeNullifiers:
    def __init__(self):
        self._seen = set()

    def seen(self, n: bytes) -> bool:
        return n in self._seen

    def record(self, n: bytes, height: int) -> None:
        self._seen.add(n)


class _FakeVerifiers:
    def __init__(self, metrics: Dict[int, ProofMetrics]):
        self.metrics = metrics

    def verify(
        self, envelope: ProofEnvelope, *, header: HeaderView, policy: PolicySnapshot
    ) -> VerificationResult:
        mt = self.metrics.get(envelope.type_id)
        if mt is None:
            return VerificationResult(
                ok=False,
                metrics=ProofMetrics(),
                normalized_body_cbor=envelope.body_cbor,
                reason="unsupported",
            )
        return VerificationResult(
            ok=True, metrics=mt, normalized_body_cbor=envelope.body_cbor
        )


class _FakeScorer:
    def __init__(self, psi_by_type: Dict[int, int]):
        self.psi = psi_by_type

    def score(
        self, *, items: Sequence[tuple[int, ProofMetrics]], policy: PolicySnapshot
    ):
        total = sum(self.psi.get(int(t), 0) for t, _ in items)
        return int(total), {"count": len(items)}


def _env(
    *,
    header: _Header,
    proofs: Iterable[ProofEnvelope],
    scorer: _FakeScorer,
    verifiers: _FakeVerifiers,
):
    policy = _Policy(
        poies_policy_root=header.poies_policy_root, alg_policy_root=header.policy_alg_root
    )
    nullifiers = _FakeNullifiers()
    return validator_mod.validate_block(
        header=header,
        proofs=list(proofs),
        policy=policy,  # type: ignore[arg-type]
        verifiers=verifiers,  # type: ignore[arg-type]
        scorer=scorer,  # type: ignore[arg-type]
        nullifiers=nullifiers,
    )


def _make_envelope(t: int, idx: int = 0) -> ProofEnvelope:
    return ProofEnvelope(type_id=t, body_cbor=b"{}", nullifier=bytes([idx % 256]) * 32)


def test_prefork_uses_legacy_rules():
    hdr = _Header(
        height=99_999,
        chain_id=1,
        theta_micro=900_000,
        poies_policy_root=b"\x01" * 32,
        policy_alg_root=b"\x02" * 32,
    )
    verifier = _FakeVerifiers(
        {PROOF_TYPE_HASHSHARE: ProofMetrics(d_ratio=3.0)}
    )
    scorer = _FakeScorer({})
    out = _env(
        header=hdr,
        proofs=[_make_envelope(PROOF_TYPE_HASHSHARE)],
        scorer=scorer,
        verifiers=verifier,
    )
    assert out.ok


def test_postfork_requires_declared_work(monkeypatch):
    hdr = _Header(
        height=100_000,
        chain_id=1,
        theta_micro=1_000_000,
        poies_policy_root=b"\x01" * 32,
        policy_alg_root=b"\x02" * 32,
    )
    verifier = _FakeVerifiers({ProofType.AI: ProofMetrics(), PROOF_TYPE_HASHSHARE: ProofMetrics(d_ratio=2.0)})
    scorer = _FakeScorer({int(ProofType.AI): 900_000})

    out = _env(
        header=hdr,
        proofs=[_make_envelope(ProofType.AI, idx=1), _make_envelope(PROOF_TYPE_HASHSHARE, idx=2)],
        scorer=scorer,
        verifiers=verifier,
    )
    assert not out.ok
    assert out.reason == "work-type-required"

    hdr.work_type = int(ProofType.AI)
    out2 = _env(
        header=hdr,
        proofs=[_make_envelope(ProofType.AI, idx=3), _make_envelope(PROOF_TYPE_HASHSHARE, idx=4)],
        scorer=scorer,
        verifiers=verifier,
    )
    assert out2.ok


def test_mismatch_between_header_and_proofs(monkeypatch):
    hdr = _Header(
        height=100_000,
        chain_id=1,
        theta_micro=700_000,
        poies_policy_root=b"\x01" * 32,
        policy_alg_root=b"\x02" * 32,
        work_type=int(ProofType.AI),
    )
    verifier = _FakeVerifiers(
        {
            ProofType.STORAGE: ProofMetrics(),
            PROOF_TYPE_HASHSHARE: ProofMetrics(d_ratio=1.2),
        }
    )
    scorer = _FakeScorer({})
    out = _env(
        header=hdr,
        proofs=[_make_envelope(ProofType.STORAGE, idx=5), _make_envelope(PROOF_TYPE_HASHSHARE, idx=6)],
        scorer=scorer,
        verifiers=verifier,
    )
    assert not out.ok
    assert out.reason == "work-mismatch"


def test_fallback_hash_work_allowed(monkeypatch):
    # Force the discovery routine to report fallback-only availability
    monkeypatch.setattr(
        validator_mod.work_registry,
        "discover_available_work_types",
        lambda **_: WorkAvailability(
            available={int(ProofType.HASH_WORK)},
            preferred=None,
            detected={int(ProofType.HASH_WORK): "proofs.hash_work"},
            fallback_only=True,
        ),
    )
    hdr = _Header(
        height=120_000,
        chain_id=1,
        theta_micro=400_000,
        poies_policy_root=b"\x01" * 32,
        policy_alg_root=b"\x02" * 32,
        work_type=int(ProofType.HASH_WORK),
    )
    verifier = _FakeVerifiers(
        {
            ProofType.HASH_WORK: ProofMetrics(ai_units=0.0),
            PROOF_TYPE_HASHSHARE: ProofMetrics(d_ratio=1.5),
        }
    )
    scorer = _FakeScorer({int(ProofType.HASH_WORK): 500_000})
    out = _env(
        header=hdr,
        proofs=[_make_envelope(ProofType.HASH_WORK, idx=7), _make_envelope(PROOF_TYPE_HASHSHARE, idx=8)],
        scorer=scorer,
        verifiers=verifier,
    )
    assert out.ok
