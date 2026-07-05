"""
ANM-6.0.0 P2P deploy-blocker regression: the mainnet P2P network fingerprint must be
STABLE across releases and source edits, or an upgraded node fragments from every peer
still on the prior release (rejected on network_params_mismatch / consensus_mismatch).

Root cause that this guards against: `compute_network_params_hash` used to hash the raw
bytes of `core/network_params.py`, so adding a *dormant* forward fork (e.g.
FORK_ROOT_COMMITMENT with activation H=37000) shifted the mainnet fingerprint even
though the fork does nothing until activation. The fix pins the mainnet fingerprint to
its canonical live value. `core/chain/identity.py::_consensus_id_fingerprint` folds the
same hash into the consensus-id, so pinning the params-hash stabilizes both.

These are GOLDEN values captured from the running mainnet node (chain 1). If a future
change legitimately alters the network identity, that is a deliberate, coordinated
event — update the pin AND this test together.
"""

import importlib

import pytest

# The canonical mainnet (chain 1) fingerprint the live network produces. Every peer
# computes this; an upgraded node MUST reproduce it byte-for-byte to handshake.
MAINNET_PARAMS_HASH_HEX = (
    "41f0acb8b3ac98ddee524a7bb1752f6af25dc596c71003fd3df4a69d899730b1"
)


@pytest.fixture()
def np():
    return importlib.import_module("core.network_params")


def test_mainnet_params_hash_matches_pinned_live_value(np):
    got = np.compute_network_params_hash(1).hex()
    assert got == MAINNET_PARAMS_HASH_HEX, (
        "mainnet P2P params-hash drifted from the live network's value; this fragments "
        "upgraded nodes from legacy peers. If the change is intentional and coordinated, "
        "update PINNED_NETWORK_PARAMS_HASH_BY_CHAIN[1] and this golden test together."
    )


def test_mainnet_is_pinned(np):
    assert 1 in np.PINNED_NETWORK_PARAMS_HASH_BY_CHAIN
    assert (
        np.PINNED_NETWORK_PARAMS_HASH_BY_CHAIN[1].hex() == MAINNET_PARAMS_HASH_HEX
    )


def test_future_inactive_fork_does_not_change_mainnet_fingerprint(np, monkeypatch):
    """The core property of the fix: appending a brand-new, not-yet-active forward fork
    to the activation schedule must NOT change the mainnet P2P fingerprint."""
    before = np.compute_network_params_hash(1)

    # Simulate a future release adding a dormant fork far in the future.
    patched = {k: dict(v) for k, v in np.ACTIVATION_HEIGHTS_BY_NETWORK.items()}
    for key in list(patched.keys()):
        if key[1] == 1:  # mainnet chain_id
            patched[key]["some_future_fork_v7"] = 10_000_000
    monkeypatch.setattr(np, "ACTIVATION_HEIGHTS_BY_NETWORK", patched, raising=True)

    after = np.compute_network_params_hash(1)
    assert before == after, (
        "adding a dormant forward fork changed the mainnet P2P fingerprint — the pin is "
        "not decoupling identity from the fork schedule"
    )


def test_consensus_id_stable_for_mainnet(np):
    """The consensus-id (which folds in the params-hash) must also be stable for a fixed
    genesis, since the handshake rejects on consensus_mismatch too."""
    identity = importlib.import_module("core.chain.identity")
    genesis = np.get_expected_genesis_hash(1)
    if not genesis:
        pytest.skip("no pinned mainnet genesis available in this environment")
    a = identity.consensus_id_from_runtime(chain_id=1, genesis_hash=genesis)
    b = identity.consensus_id_from_runtime(chain_id=1, genesis_hash=genesis)
    assert a == b
    assert a.startswith("consensus/")


def test_unpinned_chain_still_computes(np):
    """Un-pinned (dev / ephemeral) chains fall back to the computed identity and must
    still return a 32-byte digest without raising."""
    h = np.compute_network_params_hash(918273)
    assert isinstance(h, (bytes, bytearray)) and len(h) == 32
