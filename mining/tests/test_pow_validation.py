from __future__ import annotations

import hashlib

from mining.pow_validation import (digest_from_sign_bytes, derive_share_target_int,
                                   derive_share_threshold_micro, evaluate_digest)


def test_evaluate_digest_honors_share_and_block_boundaries() -> None:
    theta_micro = 1_000_000
    share_ratio = 0.5
    share_target_int = derive_share_target_int(theta_micro, share_ratio)
    block_target_int = share_target_int // 2

    at_share = evaluate_digest(
        share_target_int,
        theta_micro=theta_micro,
        share_ratio=share_ratio,
        block_target=block_target_int,
        enforce_share_target=True,
    )
    assert at_share.share_ok is True
    assert at_share.is_block is False
    assert at_share.share_target_int == share_target_int
    assert at_share.block_target_int == block_target_int

    at_block = evaluate_digest(
        block_target_int,
        theta_micro=theta_micro,
        share_ratio=share_ratio,
        block_target=block_target_int,
        enforce_share_target=True,
    )
    assert at_block.share_ok is True
    assert at_block.is_block is True

    below_share = evaluate_digest(
        share_target_int + 1,
        theta_micro=theta_micro,
        share_ratio=share_ratio,
        block_target=block_target_int,
        enforce_share_target=True,
    )
    assert below_share.share_ok is False
    assert below_share.is_block is False


def test_derive_share_threshold_micro_uses_deterministic_flooring() -> None:
    assert derive_share_threshold_micro(12_076_750, 0.01) == 120_767


def test_digest_from_sign_bytes_matches_manual_sha3() -> None:
    sign_bytes = bytes.fromhex("aa" * 32)
    mix_seed = bytes.fromhex("bb" * 32)
    nonce_int = 0x1122334455667788
    expected = hashlib.sha3_256(
        sign_bytes + mix_seed + nonce_int.to_bytes(8, "little", signed=False)
    ).digest()

    actual = digest_from_sign_bytes(
        sign_bytes,
        mix_seed=mix_seed,
        nonce_int=nonce_int,
        nonce_byteorder="little",
    )
    assert actual == expected

