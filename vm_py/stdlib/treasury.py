from __future__ import annotations

from typing import Any


def balance(address: bytes) -> int:
    """
    Deterministic stub for the VM treasury balance lookup.

    The real implementation lives in the chain runtime. For VM-Py unit tests
    we just provide a placeholder; tests that care about balances patch this
    module with a stub object.
    """
    # In real usage this would query the on-chain treasury account(s).
    return 0


def transfer(from_addr: bytes, to_addr: bytes, amount: int) -> None:
    """
    Deterministic stub for the VM treasury transfer primitive.

    In the unit tests, Escrow patches this out with a StubTreasury that records
    calls and manipulates an in-memory balance instead.
    """
    # No-op stub; real implementation would mutate balances.
    return None


__all__ = ["balance", "transfer"]
