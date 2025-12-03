"""Block processing micro-benchmark.

This script exercises the execution runtime over blocks with varying numbers of
simple transfer transactions. It relies on the runtime's apply_tx helper and
uses the existing fee/nonce/balance logic instead of re-implementing any state
transitions.

Usage:
    python tools/bench_block_processing.py
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from execution.runtime.executor import apply_tx
from execution.types.context import BlockContext, TxContext


@dataclass
class SimpleAccount:
    balance: int = 0
    nonce: int = 0


class SimpleState:
    """Minimal state backend compatible with runtime.transfer helpers."""

    def __init__(self) -> None:
        self.accounts: Dict[bytes, SimpleAccount] = {}

    def ensure_account(self, addr: bytes) -> None:
        if addr not in self.accounts:
            self.accounts[addr] = SimpleAccount()

    # Balance accessors used by apply_transfer
    def get_balance(self, addr: bytes) -> int:
        return self.accounts.get(addr, SimpleAccount()).balance

    def set_balance(self, addr: bytes, value: int) -> None:
        self.ensure_account(addr)
        self.accounts[addr].balance = int(value)

    # Nonce accessors used by apply_transfer
    def get_nonce(self, addr: bytes) -> int:
        return self.accounts.get(addr, SimpleAccount()).nonce

    def set_nonce(self, addr: bytes, value: int) -> None:
        self.ensure_account(addr)
        self.accounts[addr].nonce = int(value)

    def compute_state_root(self) -> bytes:
        """Deterministic commitment of balances/nonces (best-effort)."""
        items = sorted(self.accounts.items(), key=lambda kv: kv[0])
        buf = bytearray()
        for addr, acc in items:
            buf.extend(addr)
            buf.extend(int(acc.balance).to_bytes(16, "big", signed=False))
            buf.extend(int(acc.nonce).to_bytes(8, "big", signed=False))
        return hashlib.sha3_256(bytes(buf)).digest()


def _make_address(seed: int) -> bytes:
    return seed.to_bytes(20, "big")


def _populate_initial_balances(state: SimpleState, addrs: Iterable[bytes], balance: int) -> None:
    for addr in addrs:
        state.set_balance(addr, balance)


def _generate_transfers(senders: List[bytes], recipients: List[bytes], count: int) -> List[dict]:
    txs: List[dict] = []
    for i in range(count):
        sender = senders[i % len(senders)]
        recipient = recipients[i % len(recipients)]
        txs.append(
            {
                "kind": "transfer",
                "from": sender,
                "to": recipient,
                "value": 1,
                "gasLimit": 25_000,
            }
        )
    return txs


def _process_block(tx_bundle: List[dict], *, height: int, chain_id: int) -> float:
    state = SimpleState()
    senders = [_make_address(i + 1) for i in range(5)]
    recipients = [_make_address(100 + i) for i in range(5)]
    _populate_initial_balances(state, senders, balance=1_000_000)
    _populate_initial_balances(state, recipients, balance=0)

    block_env = BlockContext(height=height, timestamp=int(time.time()), chain_id=chain_id, coinbase=b"\x01" * 20)

    start = time.perf_counter()
    for idx, tx in enumerate(tx_bundle):
        sender = tx.get("from") or senders[idx % len(senders)]
        tx_env = TxContext(sender=sender, chain_id=chain_id, nonce=idx, gas_price=1)
        apply_tx(tx, state, block_env, params=None, tx_env=tx_env)
    end = time.perf_counter()
    return end - start


def main() -> None:
    sizes = [10, 100, 500]
    timings = []
    for height, sz in enumerate(sizes, start=1):
        txs = _generate_transfers(
            senders=[_make_address(i + 1) for i in range(8)],
            recipients=[_make_address(200 + i) for i in range(8)],
            count=sz,
        )
        elapsed = _process_block(txs, height=height, chain_id=1)
        timings.append((sz, elapsed))

    print("Block size | processing time (s)")
    print("-----------|---------------------")
    for size, elapsed in timings:
        print(f"{size:10d} | {elapsed:0.6f}")


if __name__ == "__main__":
    main()
