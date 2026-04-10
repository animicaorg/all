from __future__ import annotations

from omni_sdk.tx.build import transfer
from omni_sdk.tx.signing import sign_transaction


def _sample_tx():
    return transfer(
        from_addr="anim1source",
        to_addr="anim1dest",
        amount=123,
        nonce=7,
        gas_limit=21_000,
        max_fee=1_000_000_000,
        chain_id=1,
    )


def test_sign_transaction_passes_fork_id_when_supported() -> None:
    class _SignerWithFork:
        alg_id = 4098
        public_key = b"pk"

        def __init__(self) -> None:
            self.calls: list[tuple[bytes, int, int | None]] = []

        def sign_tx(
            self, message: bytes, chain_id: int, fork_id: int | None = None
        ) -> bytes:
            self.calls.append((message, chain_id, fork_id))
            return b"sig-with-fork"

    signer = _SignerWithFork()
    signed = sign_transaction(_sample_tx(), signer, chain_id=1, fork_id=42)

    assert signed.signature == b"sig-with-fork"
    assert signer.calls
    assert signer.calls[0][1] == 1
    assert signer.calls[0][2] == 42


def test_sign_transaction_falls_back_when_signer_has_no_fork_id() -> None:
    class _SignerNoFork:
        alg_id = 4098
        public_key = b"pk"

        def __init__(self) -> None:
            self.calls: list[tuple[bytes, int]] = []

        def sign_tx(self, message: bytes, chain_id: int) -> bytes:
            self.calls.append((message, chain_id))
            return b"sig-no-fork"

    signer = _SignerNoFork()
    signed = sign_transaction(_sample_tx(), signer, chain_id=1, fork_id=99)

    assert signed.signature == b"sig-no-fork"
    assert signer.calls
    assert signer.calls[0][1] == 1
