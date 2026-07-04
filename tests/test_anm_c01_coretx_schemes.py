"""ANM-C01 (coretx stack): forgeable signature schemes 1-4 disabled; mainnet
requires only the real FIPS-204 ml_dsa_65 (id 11).

This is the second signature stack (coretx/schemes.py + coretx/crypto.py). The
node-local allowlist in animica.tx.signing covers the pq.py stack; this test
covers coretx, whose _CHAIN_REQUIRED_SCHEMES previously *forced* the forgeable
stubs 1 & 2 to stay enabled on mainnet.
"""
from coretx.schemes import (
    CANONICAL_SCHEME_SPECS,
    evaluate_scheme_policy,
    load_policy_override,
    required_schemes_for_chain,
)


def _spec(sid):
    return next(s for s in CANONICAL_SCHEME_SPECS if s.scheme_id == sid)


def test_mainnet_requires_only_real_scheme():
    req = required_schemes_for_chain(1)
    assert req == (11,), req
    assert 1 not in req and 2 not in req


def test_stub_schemes_disabled_by_default():
    for sid in (1, 2, 3, 4):
        assert _spec(sid).enabled_by_default is False, sid
    assert _spec(11).enabled_by_default is True


def test_policy_rejects_stubs_accepts_real():
    override = load_policy_override()
    for sid in (1, 2, 3, 4):
        ev = evaluate_scheme_policy(_spec(sid), disabled_by_policy=set(), override=override)
        assert ev.enabled_effective is False, sid
    ev11 = evaluate_scheme_policy(_spec(11), disabled_by_policy=set(), override=override)
    assert ev11.enabled_effective is True
