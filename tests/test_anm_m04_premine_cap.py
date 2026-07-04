"""ANM-M04: alloc-cap enforced for new networks, grandfathered for shipped ones."""
import pytest

from core.genesis.loader import GenesisError, _validate_genesis


def _g(chain_id, premine, allocs):
    return {
        "chainId": chain_id,
        "genesisTime": 0,
        "alloc": [{"address": f"anim1{i}", "balance": b} for i, b in enumerate(allocs)],
        "economics": {"premineTotal": str(premine)},
        "consensus": {},
    }


def test_grandfathered_mainnet_premine0_with_alloc_loads():
    # chainId=1, premineTotal=0, alloc=81M -> grandfathered (warns, no raise)
    _validate_genesis(_g(1, 0, [81_000_000]))


def test_new_network_premine0_with_alloc_rejected():
    with pytest.raises(GenesisError, match="exceeds premineTotal"):
        _validate_genesis(_g(9999, 0, [1000]))


def test_new_network_alloc_within_premine_ok():
    _validate_genesis(_g(9999, 5000, [1000, 2000]))


def test_new_network_alloc_exceeds_premine_rejected():
    with pytest.raises(GenesisError, match="exceeds premineTotal"):
        _validate_genesis(_g(9999, 1000, [2000]))
