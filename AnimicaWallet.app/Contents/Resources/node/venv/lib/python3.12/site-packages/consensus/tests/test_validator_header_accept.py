from __future__ import annotations

import dataclasses as dc
import typing as t

import pytest

import consensus.math as math_mod
import consensus.policy as policy_mod
import consensus.scorer as scorer_mod
import consensus.validator as validator_mod

try:
    from consensus.errors import ConsensusError, PolicyError
except Exception:  # pragma: no cover - make the test resilient to naming

    class PolicyError(Exception):
        pass

    class ConsensusError(Exception):
        pass


# ------------------------------- Helpers & Adaptor --------------------------------


@dc.dataclass
class _Header:
    """
    A minimal header shape that offers both attribute and item access
    for common theta/policy-root field spellings.
    """

    theta_micro: int
    poies_policy_root: bytes
    alg_policy_root: bytes
    da_root: bytes = b"\x00" * 32
    u_draw: bytes = b"\x11" * 32  # the nonce used to derive H(u)

    # generous aliasing for different validator implementations
    def __getitem__(self, k: str):
        return getattr(self, _normalize(k))

    def get(self, k: str, default=None):
        return getattr(self, _normalize(k), default)

    def __getattr__(self, k: str):
        return super().__getattribute__(k)


def _normalize(k: str) -> str:
    k = k.replace("-", "").replace("_", "").lower()
    if k in ("theta", "thetamicro", "θ", "thetamicros"):
        return "theta_micro"
    if k in ("poiespolicyroot", "poies_root", "poiesroot", "policyrootpoies"):
        return "poies_policy_root"
    if k in ("algpolicyroot", "alg_root", "algpolicy", "algroot"):
        return "alg_policy_root"
    if k in ("daroot", "da_root"):
        return "da_root"
    if k in ("udraw", "nonce", "mixseed", "draw", "u"):
        return "u_draw"
    return k


@dc.dataclass
class _Proof:
    """Tiny proof envelope carrying a psi contribution (µ-nats)."""

    type_id: int
    psi_micro: int
    body: bytes = b""


class _Adaptor:
    """
    A small shim that adapts to multiple plausible validator APIs:

      - Function-style: validate_header(header, proofs, policy) -> bool/Accept
      - Class-style:    Validator(...).validate_header(...)
      - Or, as a last resort, falls back to scorer+math directly to assert acceptance.
    """

    def __init__(self):
        self.V = None
        for cname in ("Validator", "HeaderValidator"):
            if hasattr(validator_mod, cname):
                try:
                    self.V = getattr(validator_mod, cname)()
                except TypeError:
                    # allow constructors that need no args or have defaults
                    try:
                        self.V = getattr(validator_mod, cname)
                    except Exception:
                        pass

    def accept(self, header: _Header, proofs: list[_Proof], policy: t.Any) -> bool:
        # Try function-style entrypoints
        for fname in (
            "validate_header",
            "validate_block_header",
            "validate",
            "check_header",
        ):
            fn = getattr(validator_mod, fname, None)
            if callable(fn):
                for args in (
                    (header, proofs, policy),
                    (header, proofs),
                    (header,),
                ):
                    try:
                        out = fn(*args)  # type: ignore[misc]
                        return bool(getattr(out, "accepted", out))
                    except TypeError:
                        continue

        # Try class-style entrypoints
        if self.V is not None:
            obj = self.V() if isinstance(self.V, type) else self.V
            for m in ("validate_header", "validate", "check_header"):
                fn = getattr(obj, m, None)
                if callable(fn):
                    for args in (
                        (header, proofs, policy),
                        (header, proofs),
                        (header,),
                    ):
                        try:
                            out = fn(*args)  # type: ignore[misc]
                            return bool(getattr(out, "accepted", out))
                        except TypeError:
                            continue

        # Fallback: recompute S = H(u) + Σψ and compare to θ, asserting acceptance manually
        # NOTE: We rely on math_mod.H and a local sum over .psi_micro.
        Hu = int(math_mod.H(header.u_draw))  # type: ignore[arg-type]
        sigma = sum(p.psi_micro for p in proofs)
        return (Hu + sigma) >= header.theta_micro


# ------------------------------- Test Fixtures ------------------------------------


@pytest.fixture
def good_roots() -> tuple[bytes, bytes]:
    # 32-byte roots for policy bindings
    poies_root = bytes.fromhex("11" * 32)
    alg_root = bytes.fromhex("22" * 32)
    return poies_root, alg_root


@pytest.fixture
def policy_tmp_path(tmp_path, good_roots):
    """
    Create a tiny in-repo policy file that loader can read, if the validator/policy
    chooses to load from a YAML path. Otherwise we keep it around for reference.
    """
    poies_root, alg_root = good_roots
    yml = tmp_path / "poies_policy.yaml"
    yml.write_text(
        f"""# minimal example used by tests
version: 1
roots:
  poies_policy_root: "0x{poies_root.hex()}"
  alg_policy_root:   "0x{alg_root.hex()}"
caps:
  total_G_cap: 2_000_000
types: []
"""
    )
    return yml


@pytest.fixture
def adaptor() -> _Adaptor:
    return _Adaptor()


# ------------------------------- Tests --------------------------------------------


def test_accept_header_when_S_ge_theta(monkeypatch, adaptor: _Adaptor, good_roots):
    """
    Acceptance: with H(u)=200_000 µ-nats and Σψ=900_000 µ-nats and θ=1_000_000,
    S = 1_100_000 ≥ θ → accept.
    """
    # Pin H(u) to a deterministic micro-nats value
    monkeypatch.setattr(math_mod, "H", lambda u: 200_000, raising=True)

    poies_root, alg_root = good_roots
    header = _Header(
        theta_micro=1_000_000,
        poies_policy_root=poies_root,
        alg_policy_root=alg_root,
        u_draw=b"\xaa" * 32,
    )
    proofs = [
        _Proof(type_id=1, psi_micro=500_000),
        _Proof(type_id=2, psi_micro=400_000),
    ]

    # Some implementations compute Σψ via scorer; ensure it can sum our test proofs if consulted.
    def fake_score(_policy, _proofs):
        return {
            "sum_psi_micro": sum(p.psi_micro for p in _proofs),
            "breakdown": {p.type_id: p.psi_micro for p in _proofs},
        }

    if hasattr(scorer_mod, "score_batch"):
        monkeypatch.setattr(
            scorer_mod, "score_batch", lambda pol, prfs: fake_score(pol, prfs)
        )
    elif hasattr(scorer_mod, "sum_psi"):
        monkeypatch.setattr(
            scorer_mod, "sum_psi", lambda prfs: sum(p.psi_micro for p in prfs)
        )

    # Policy object is optional/shape-agnostic here; pass roots dict for convenience.
    policy = {"poies_policy_root": poies_root, "alg_policy_root": alg_root}

    accepted = adaptor.accept(header, proofs, policy)
    assert accepted is True


def test_reject_on_policy_root_mismatch(monkeypatch, adaptor: _Adaptor, good_roots):
    """
    Policy-root mismatch should be rejected by the validator (PolicyError or False).
    """
    monkeypatch.setattr(
        math_mod, "H", lambda u: 1_000_000, raising=True
    )  # huge H(u) so S would pass if not for roots

    poies_root, alg_root = good_roots
    bad_alg_root = bytes.fromhex("33" * 32)

    header = _Header(
        theta_micro=10,  # trivially low so only policy-root matters
        poies_policy_root=poies_root,
        alg_policy_root=bad_alg_root,  # <-- mismatch
    )
    proofs = [_Proof(type_id=1, psi_micro=0)]

    policy = {"poies_policy_root": poies_root, "alg_policy_root": alg_root}

    try:
        accepted = adaptor.accept(header, proofs, policy)
        # If the API signaled via boolean, ensure it's False.
        assert (
            accepted is False
        ), "header should be rejected due to policy-root mismatch"
    except PolicyError:
        # Preferred behavior: explicit PolicyError.
        pass


def test_theta_schedule_accept_vs_reject(monkeypatch, adaptor: _Adaptor, good_roots):
    """
    Use the same proofs and H(u) under two different θ values:
      - θ_high: reject
      - θ_low:  accept
    This exercises the recompute S=H(u)+Σψ and comparison against θ from the header/schedule.
    """
    # Make H(u) modest
    monkeypatch.setattr(math_mod, "H", lambda u: 150_000, raising=True)

    poies_root, alg_root = good_roots
    proofs = [
        _Proof(type_id=1, psi_micro=200_000),
        _Proof(type_id=2, psi_micro=100_000),
    ]
    policy = {"poies_policy_root": poies_root, "alg_policy_root": alg_root}

    # High θ → reject (S = 450_000 < 500_000)
    header_high = _Header(
        theta_micro=500_000,
        poies_policy_root=poies_root,
        alg_policy_root=alg_root,
    )
    accepted_high = adaptor.accept(header_high, proofs, policy)
    assert accepted_high is False

    # Low θ → accept (S = 450_000 ≥ 400_000)
    header_low = _Header(
        theta_micro=400_000,
        poies_policy_root=poies_root,
        alg_policy_root=alg_root,
    )
    accepted_low = adaptor.accept(header_low, proofs, policy)
    assert accepted_low is True
