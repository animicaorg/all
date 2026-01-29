from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List


@dataclass
class Account:
    nonce: int = 0
    balance: int = 0
    code_hash: bytes = field(default_factory=lambda: b"\x00" * 32)


ALICE = "0x" + "aa" * 20
BOB = "0x" + "bb" * 20
CAROL = "0x" + "cc" * 20
COINBASE = "0x" + "99" * 20


@dataclass
class Tx:
    sender: str
    to: str
    value: int
    gas_limit: int = 21_000
    gas_price: int = 1


@dataclass
class Block:
    height: int
    txs: List[Tx]
    coinbase: str = COINBASE


def mk_genesis_state() -> Dict[str, Account]:
    return {
        ALICE: Account(balance=2_000_000_000_000_000_000),
        BOB: Account(balance=750_000_000_000_000_000),
        CAROL: Account(balance=0),
        COINBASE: Account(balance=0),
    }


def state_root(state: Dict[str, Account]) -> str:
    h = hashlib.sha3_256()
    for addr in sorted(state.keys()):
        acct = state[addr]
        h.update(bytes.fromhex(addr[2:]))
        h.update(acct.nonce.to_bytes(8, "big"))
        h.update(acct.balance.to_bytes(32, "big"))
        h.update(acct.code_hash)
    return "0x" + h.hexdigest()


def apply_transfer(state: Dict[str, Account], tx: Tx, coinbase: str) -> None:
    fee = tx.gas_limit * tx.gas_price
    sender = state[tx.sender]
    if sender.balance < tx.value + fee:
        raise RuntimeError("InsufficientBalance")

    sender.balance -= tx.value + fee
    sender.nonce += 1

    state[tx.to].balance += tx.value
    state[coinbase].balance += fee


def apply_block(state: Dict[str, Account], block: Block) -> None:
    for tx in block.txs:
        apply_transfer(state, tx, block.coinbase)


def build_chain() -> List[Block]:
    return [
        Block(
            height=1,
            txs=[
                Tx(ALICE, BOB, 400_000_000_000_000_000),
                Tx(ALICE, CAROL, 150_000_000_000_000_000, gas_price=2),
            ],
        ),
        Block(
            height=2,
            txs=[
                Tx(BOB, CAROL, 50_000_000_000_000_000),
                Tx(CAROL, ALICE, 10_000_000_000_000_000, gas_limit=30_000),
            ],
        ),
        Block(
            height=3,
            txs=[
                Tx(ALICE, BOB, 100_000_000_000_000_000),
                Tx(BOB, ALICE, 25_000_000_000_000_000, gas_price=3),
            ],
        ),
    ]


def _balances(state: Dict[str, Account], addrs: Iterable[str]) -> Dict[str, int]:
    return {addr: state[addr].balance for addr in addrs}


def test_chain_execution_is_deterministic_across_state_copies() -> None:
    chain = build_chain()
    state_a = mk_genesis_state()
    state_b = copy.deepcopy(state_a)

    for blk in chain:
        apply_block(state_a, blk)
        apply_block(state_b, blk)

    assert state_root(state_a) == state_root(state_b)

    watched = [ALICE, BOB, CAROL, COINBASE]
    assert _balances(state_a, watched) == _balances(state_b, watched)

    for addr in watched:
        assert state_a[addr].nonce == state_b[addr].nonce
