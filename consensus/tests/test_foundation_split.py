# SPDX-License-Identifier: Apache-2.0
"""
FORK_FOUNDATION_SPLIT (7.1.0) — forward-only 85% miner / 15% foundation-treasury
block-subsidy split, activating on mainnet at block 42,001.

These tests pin the consensus-critical properties:
  * grandfathered below H (byte-identical to the pre-7.1.0 100%-miner emission);
  * exact 85/15 split at/after H, emission-conserving (miner+treasury == total);
  * deterministic (code-committed constants, integer-only math, no float/env/wallclock);
  * the 15% is routed to a VALID foundation address whose credit key matches the
    miner-reward / getbalance key (so the funds are actually visible and spendable);
  * testnet/devnet and the pinned mainnet P2P params-hash are unaffected.
"""

from __future__ import annotations

import pytest

from consensus.rewards import (
    compute_block_reward,
    FOUNDATION_TREASURY_ADDRESS,
    FOUNDATION_TREASURY_SPLIT_PCT,
)
from core.chain.block_import import _load_full_params_dict

MAINNET = 1
H = 42_001  # FORK_FOUNDATION_SPLIT mainnet activation height
FULL_SUBSIDY = 300_000_000_000  # 300 ANM in nANM (genesis epoch)


@pytest.fixture(scope="module")
def mparams():
    p = _load_full_params_dict(MAINNET)
    assert p, "mainnet params must load from spec/params.yaml"
    return p


def _outs(height, params):
    return dict(compute_block_reward(chain_id=MAINNET, height=height, params=params, canonical_height=height))


# ---------------------------------------------------------------------------
# Grandfathering (forward-only): below H the emission is unchanged (100% miner)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("height", [1, 100, 41_000, 42_000])
def test_grandfathered_below_activation_is_100pct_miner(height, mparams):
    outs = _outs(height, mparams)
    # exactly one recipient, no foundation output, full subsidy to the miner
    assert FOUNDATION_TREASURY_ADDRESS not in outs
    assert len(outs) == 1
    assert sum(outs.values()) == FULL_SUBSIDY


def test_activation_boundary_is_exact_at_H(mparams):
    # 42,000 = still 100% miner; 42,001 = split active (one block apart, not co-located
    # with the freeze at 42,000).
    assert FOUNDATION_TREASURY_ADDRESS not in _outs(H - 1, mparams)
    assert FOUNDATION_TREASURY_ADDRESS in _outs(H, mparams)


# ---------------------------------------------------------------------------
# The split itself: exact 255/45 at H, emission-conserving everywhere
# ---------------------------------------------------------------------------

def test_split_at_activation_255_45(mparams):
    outs = _outs(H, mparams)
    total = sum(outs.values())
    foundation = outs[FOUNDATION_TREASURY_ADDRESS]
    miner = total - foundation
    assert total == FULL_SUBSIDY               # base subsidy unchanged (still 300 ANM)
    assert foundation == 45_000_000_000        # 15% of 300 ANM
    assert miner == 255_000_000_000            # 85% of 300 ANM


@pytest.mark.parametrize("height", [H, H + 1, 1_350_001, 2_700_001, 4_050_001, 6_750_001])
def test_split_conserves_and_is_exact_across_halvings(height, mparams):
    outs = _outs(height, mparams)
    total = sum(outs.values())
    foundation = outs.get(FOUNDATION_TREASURY_ADDRESS, 0)
    miner = total - foundation
    # conservation: nothing minted or burned by the split
    assert miner + foundation == total
    # treasury is exactly the integer floor of 15% (remainder favors nobody unfairly:
    # miner gets total - floor, so the two always sum to total)
    assert foundation == (total * FOUNDATION_TREASURY_SPLIT_PCT) // 100


def test_total_emission_unchanged_by_split(mparams):
    # The split must NOT change per-block issuance — only its division. Compare the
    # sum at/after H to the pre-split subsidy for the same height (same epoch).
    for height in [H, 1_350_001, 2_700_001]:
        post = sum(_outs(height, mparams).values())
        # the pre-split total is the same epoch subsidy the grandfathered path emits;
        # emulate it by reading a below-H height in the SAME epoch is not possible, so
        # assert against the closed-form epoch subsidy instead.
        from consensus.rewards import parse_emission_schedule, _subsidy_total_for_height
        expected = _subsidy_total_for_height(height, parse_emission_schedule(mparams))
        assert post == expected


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_deterministic_repeated_calls(mparams):
    a = compute_block_reward(chain_id=MAINNET, height=H, params=mparams, canonical_height=H)
    b = compute_block_reward(chain_id=MAINNET, height=H, params=mparams, canonical_height=H)
    assert a == b


def test_split_math_is_integer_only():
    # 15% of any integer total via // is float-free and conserving for a wide sweep.
    for total in [FULL_SUBSIDY, 150_000_000_000, 1, 7, 999_999_999, 100_000, 2**40 + 3]:
        treasury = (total * FOUNDATION_TREASURY_SPLIT_PCT) // 100
        miner = total - treasury
        assert isinstance(treasury, int) and isinstance(miner, int)
        assert miner + treasury == total
        assert treasury <= total  # never over-pays the treasury


# ---------------------------------------------------------------------------
# Env override retunes the activation height (operator escape hatch)
# ---------------------------------------------------------------------------

def test_env_override_retunes_boundary(mparams, monkeypatch):
    monkeypatch.setenv("ANIMICA_FORK_FOUNDATION_SPLIT_HEIGHT", "50000")
    # at the default H the split is now INACTIVE; it moves to 50,000
    assert FOUNDATION_TREASURY_ADDRESS not in _outs(H, mparams)
    assert FOUNDATION_TREASURY_ADDRESS not in _outs(49_999, mparams)
    assert FOUNDATION_TREASURY_ADDRESS in _outs(50_000, mparams)


# ---------------------------------------------------------------------------
# Instant blocks + genesis are untouched
# ---------------------------------------------------------------------------

def test_instant_block_zero_reward(mparams):
    assert compute_block_reward(chain_id=MAINNET, height=H, params=mparams, instant_block=True) == []


def test_genesis_is_premine_not_split(mparams):
    outs = compute_block_reward(chain_id=MAINNET, height=0, params=mparams)
    assert FOUNDATION_TREASURY_ADDRESS not in dict(outs)
    assert len(outs) == 1  # single premine output


# ---------------------------------------------------------------------------
# Foundation address is valid + the credit key matches miner-reward / getbalance
# ---------------------------------------------------------------------------

def test_foundation_address_decodes_to_valid_32byte_key():
    from core.utils.address import address_to_bytes
    key = address_to_bytes(FOUNDATION_TREASURY_ADDRESS)
    assert len(key) == 32
    assert key.hex() == "0de5187830c1493cb6ce5341e2cf23901084cffd6cff37e404c727a301036229"


def test_credit_key_parity_with_miner_reward_decoder():
    # block_import credits the treasury via core.utils.address.address_to_bytes; the
    # miner is credited (and getbalance reads) via rpc.methods.miner._decode_bech32_address.
    # If these disagree the 15% would be credited to an unreadable/dead key.
    from core.utils.address import address_to_bytes
    from rpc.methods.miner import _decode_bech32_address
    assert address_to_bytes(FOUNDATION_TREASURY_ADDRESS) == _decode_bech32_address(FOUNDATION_TREASURY_ADDRESS)


def test_credit_roundtrip_is_visible_on_balance_key():
    # Prove a credit to the foundation key is read back by get_balance on the SAME key
    # the miner-reward path / getbalance would use — i.e. the funds are spendable.
    from core.utils.address import address_to_bytes
    from rpc.methods.miner import _decode_bech32_address
    from execution.state.apply_balance import credit, begin_apply_block, end_apply_block

    class DictState:
        def __init__(self):
            self._b = {}
        def get_balance(self, addr):
            return self._b.get(bytes(addr), 0)
        def set_balance(self, addr, v):
            self._b[bytes(addr)] = int(v)

    st = DictState()
    begin_apply_block(H, None)
    try:
        credit(st, address_to_bytes(FOUNDATION_TREASURY_ADDRESS), 45_000_000_000,
               reason="TEST_FOUNDATION", height=H)
    finally:
        end_apply_block()
    assert st.get_balance(address_to_bytes(FOUNDATION_TREASURY_ADDRESS)) == 45_000_000_000
    # read via the miner/getbalance decoder key — must see the same balance
    assert st.get_balance(_decode_bech32_address(FOUNDATION_TREASURY_ADDRESS)) == 45_000_000_000


# ---------------------------------------------------------------------------
# Isolation: other chains + the pinned mainnet P2P identity are unaffected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chain_id", [1337, 2])
def test_other_chains_do_not_get_foundation_output(chain_id):
    # The foundation split is chain_id==1-guarded; testnet/devnet follow their own
    # params split and never emit the mainnet foundation address.
    params = _load_full_params_dict(chain_id)
    if not params:
        pytest.skip(f"no params for chain {chain_id}")
    for height in [1, H, 1_350_001]:
        outs = dict(compute_block_reward(chain_id=chain_id, height=height, params=params, canonical_height=height))
        assert FOUNDATION_TREASURY_ADDRESS not in outs


def test_mainnet_p2p_params_hash_pin_unchanged():
    # Adding FORK_FOUNDATION_SPLIT + editing rewards.py must NOT change the pinned
    # mainnet params-hash, or 7.1.0 and 7.0.0/6.x nodes would stop peering.
    from core.network_params import compute_network_params_hash
    assert compute_network_params_hash(1).hex() == (
        "41f0acb8b3ac98ddee524a7bb1752f6af25dc596c71003fd3df4a69d899730b1"
    )
