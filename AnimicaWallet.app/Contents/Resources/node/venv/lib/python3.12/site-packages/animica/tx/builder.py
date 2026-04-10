from __future__ import annotations

from dataclasses import dataclass

from .types import TxBody


@dataclass(frozen=True)
class NonceState:
    confirmed_nonce: int
    pending_outgoing_nonce: int


def select_next_nonce(state: NonceState) -> int:
    return max(int(state.confirmed_nonce), int(state.pending_outgoing_nonce)) + 1


def build_tx_body(*, chain_id: int, nonce_state: NonceState, from_addr: str, to_addr: str, value: int, fee: int, memo: str = "", valid_after: int = 0, valid_until: int = 0) -> TxBody:
    return TxBody(
        chain_id=int(chain_id),
        nonce=select_next_nonce(nonce_state),
        from_addr=from_addr,
        to_addr=to_addr,
        value=int(value),
        fee=int(fee),
        memo=memo,
        valid_after=int(valid_after),
        valid_until=int(valid_until),
    )
