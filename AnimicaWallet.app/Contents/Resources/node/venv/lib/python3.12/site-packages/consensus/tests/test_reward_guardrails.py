from consensus.rewards import compute_block_reward


def _params(split_miner=95, split_aicf=5, split_treasury=0):
    return {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 300000000000,
                    "epoch_length_blocks": 1350000,
                    "decay_pct_per_epoch": 50.0,
                    "tail_nANM_per_block": 100000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": split_miner,
                    "aicf": split_aicf,
                    "treasury": split_treasury,
                },
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "aicf_treasury": "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "treasury": "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        },
    }


def test_mainnet_secondary_outputs_disabled_by_default():
    rewards = compute_block_reward(chain_id=1, height=1, params=_params())
    assert len(rewards) == 1


def test_mainnet_dev_fee_requires_explicit_valid_non_zero_address():
    params = _params()
    params["dev_fee_enabled"] = True
    params["dev_fee_address"] = "0x" + ("0" * 64)
    params["dev_fee_bps"] = 500

    rewards = compute_block_reward(chain_id=1, height=1, params=params)
    assert rewards == []


def test_mainnet_dev_fee_enabled_splits_reward_into_two_outputs():
    params = _params(split_miner=100, split_aicf=0, split_treasury=0)
    params["dev_fee_enabled"] = True
    params["dev_fee_address"] = "anim1devfeexxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    params["dev_fee_bps"] = 500

    rewards = compute_block_reward(chain_id=1, height=1, params=params)
    assert len(rewards) == 2
    total = sum(amt for _, amt in rewards)
    assert total == 300000000000
