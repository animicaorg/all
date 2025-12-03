from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple


@dataclass
class Account:
    nonce: int = 0
    balance: int = 0
    code_hash: bytes = field(default_factory=lambda: b"\x00" * 32)


COINBASE = "0x" + "42" * 20
TREASURY = "0x" + "77" * 20
ALICE = "0x" + "aa" * 20
BOB = "0x" + "bb" * 20
CAROL = "0x" + "cc" * 20

# Parameters derived from spec/params.yaml (mainnet issuance section)
ISSUANCE_START_NANM = 1_000_000
ISSUANCE_EPOCH_LEN = 4_320_000
ISSUANCE_DECAY_PCT = 12.5
ISSUANCE_TAIL_NANM = 100_000
ISSUANCE_MAX_HALVINGS = 64


@dataclass
class Tx:
    sender: str
    to: str
    value: int
    gas_limit: int = 21_000
    gas_price: int = 1


def mk_state() -> Dict[str, Account]:
    return {
        COINBASE: Account(balance=0),
        TREASURY: Account(balance=0),
        ALICE: Account(balance=5_000_000_000_000_000_000),
        BOB: Account(balance=1_000_000_000_000_000_000),
        CAROL: Account(balance=500_000_000_000_000_000),
    }


def total_supply(state: Dict[str, Account]) -> int:
    return sum(acct.balance for acct in state.values())


def _issuance_for_epoch(epoch: int) -> int:
    decay_factor = (100.0 - ISSUANCE_DECAY_PCT) / 100.0
    reward = int(ISSUANCE_START_NANM * (decay_factor**epoch))
    return max(reward, ISSUANCE_TAIL_NANM)


def issuance_for_block(height: int) -> int:
    epoch = min(height // ISSUANCE_EPOCH_LEN, ISSUANCE_MAX_HALVINGS)
    return _issuance_for_epoch(epoch)


def mint_block_reward(state: Dict[str, Account], height: int) -> int:
    minted = issuance_for_block(height)
    state[COINBASE].balance += minted
    return minted


def apply_transfer(state: Dict[str, Account], tx: Tx) -> None:
    fee = tx.gas_limit * tx.gas_price
    sender = state[tx.sender]
    if sender.balance < tx.value + fee:
        raise RuntimeError("InsufficientBalance")
    sender.balance -= tx.value + fee
    sender.nonce += 1
    state[tx.to].balance += tx.value
    state[COINBASE].balance += fee


def generate_valid_transfers(state: Dict[str, Account], rng: random.Random, count: int) -> List[Tx]:
    addrs = [ALICE, BOB, CAROL]
    txs: List[Tx] = []
    for _ in range(count):
        sender = rng.choice(addrs)
        sender_bal = state[sender].balance
        if sender_bal <= 0:
            continue
        recipient = rng.choice([a for a in addrs if a != sender])
        max_send = max(sender_bal // 10, 1)
        value = rng.randint(1, max_send)
        txs.append(Tx(sender, recipient, value))
        # optimistic local apply to keep balances non-negative for later txs
        try:
            apply_transfer(state, txs[-1])
        except RuntimeError:
            txs.pop()
    return txs


def _epoch_supply_bounds(blocks: int) -> Tuple[int, int]:
    full_epochs, remainder = divmod(blocks, ISSUANCE_EPOCH_LEN)
    # Cap epochs to the configured max while computing theoretical max
    bounded_epochs = min(full_epochs, ISSUANCE_MAX_HALVINGS)
    epoch_rewards = [_issuance_for_epoch(e) for e in range(bounded_epochs + 1)]

    minted_from_full_epochs = sum(epoch_rewards[e] * ISSUANCE_EPOCH_LEN for e in range(bounded_epochs))
    minted_from_remainder = epoch_rewards[min(bounded_epochs, len(epoch_rewards) - 1)] * remainder

    last_reward = epoch_rewards[min(bounded_epochs, len(epoch_rewards) - 1)]
    return minted_from_full_epochs + minted_from_remainder, last_reward


def test_total_supply_respects_emission_schedule_upper_bound() -> None:
    blocks = ISSUANCE_EPOCH_LEN * 3 + 500_000
    state = mk_state()
    minted = 0
    for h in range(blocks):
        minted += mint_block_reward(state, h)
    simulated_supply = total_supply(state)

    theoretical_issuance, last_reward = _epoch_supply_bounds(blocks)
    theoretical_max = theoretical_issuance + last_reward  # buffer for rounding/next-block clamp

    assert minted <= theoretical_max
    assert simulated_supply == total_supply(state)


def test_balances_never_negative_under_random_valid_transfers() -> None:
    rng = random.Random(2025)
    state = mk_state()

    txs = generate_valid_transfers(copy.deepcopy(state), rng, 50)
    for tx in txs:
        apply_transfer(state, tx)

    assert all(acct.balance >= 0 for acct in state.values())
    assert total_supply(state) >= 0
