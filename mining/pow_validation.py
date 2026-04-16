from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
from typing import Any

from core.utils.pow import micro_threshold_to_target256

UINT256_MAX = (1 << 256) - 1


def int_from_value(value: Any, *, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        if isinstance(value, str) and value.startswith("0x"):
            return int(value, 16)
        return int(value)
    except Exception:
        return default


def _parse_decimal(value: Any, *, default: Decimal) -> Decimal:
    if value in (None, ""):
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def normalize_share_ratio(value: Any, *, default: float = 1.0) -> float:
    default_decimal = _parse_decimal(default, default=Decimal("1"))
    if default_decimal <= 0:
        default_decimal = Decimal("1")
    ratio = _parse_decimal(value, default=default_decimal)
    if ratio <= 0:
        ratio = default_decimal
    if ratio <= 0:
        ratio = Decimal("1")
    if ratio > 1:
        ratio = Decimal("1")
    return float(ratio)


def derive_share_threshold_micro(theta_micro: int, share_ratio: Any) -> int:
    theta = max(int(theta_micro), 0)
    if theta <= 0:
        return 0
    ratio = Decimal(str(normalize_share_ratio(share_ratio)))
    with localcontext() as ctx:
        ctx.prec = 50
        threshold = (Decimal(theta) * ratio).to_integral_value(rounding=ROUND_FLOOR)
    return max(1, int(threshold))


def derive_share_target_int(theta_micro: int, share_ratio: Any) -> int:
    threshold = derive_share_threshold_micro(theta_micro, share_ratio)
    if threshold <= 0:
        return 0
    try:
        return int(micro_threshold_to_target256(int(threshold)))
    except Exception:
        return 0


def derive_block_target_int(target_value: Any) -> int:
    return max(0, int_from_value(target_value, default=0))


def parse_nonce_int(nonce: Any, *, default: int = -1) -> int:
    parsed = int_from_value(nonce, default=default)
    if parsed < 0:
        return default
    if parsed > 0xFFFFFFFFFFFFFFFF:
        return default
    return parsed


def parse_hex_bytes(value: Any, *, default: bytes = b"") -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("0x"):
            text = text[2:]
        if not text:
            return default
        if len(text) % 2:
            text = "0" + text
        try:
            return bytes.fromhex(text)
        except Exception:
            return default
    return default


def digest_from_sign_bytes(
    sign_bytes: bytes,
    *,
    mix_seed: bytes = b"",
    nonce_int: int,
    nonce_byteorder: str = "little",
) -> bytes:
    nonce_bytes = int(nonce_int).to_bytes(8, nonce_byteorder, signed=False)
    hasher = hashlib.sha3_256()
    hasher.update(sign_bytes)
    hasher.update(mix_seed)
    hasher.update(nonce_bytes)
    return hasher.digest()


def digest_to_int(digest: bytes) -> int:
    if len(digest) != 32:
        raise ValueError("digest must be 32 bytes")
    return int.from_bytes(digest, "big", signed=False)


@dataclass(frozen=True)
class PowTargetDecision:
    digest_int: int
    digest_hex: str
    theta_micro: int
    share_ratio: float
    share_threshold_micro: int
    share_target_int: int
    block_target_int: int
    share_ok: bool
    is_block: bool


def evaluate_digest(
    digest: bytes | int,
    *,
    theta_micro: int,
    share_ratio: Any,
    block_target: Any,
    enforce_share_target: bool = True,
) -> PowTargetDecision:
    digest_int = int(digest) if isinstance(digest, int) else digest_to_int(digest)
    digest_hex = "0x" + digest_int.to_bytes(32, "big", signed=False).hex()
    theta = max(int(theta_micro), 0)
    ratio = normalize_share_ratio(share_ratio)
    share_threshold_micro = derive_share_threshold_micro(theta, ratio)
    share_target_int = derive_share_target_int(theta, ratio)
    block_target_int = derive_block_target_int(block_target)

    if share_target_int <= 0:
        share_ok = not enforce_share_target
    else:
        share_ok = digest_int <= share_target_int
    is_block = block_target_int > 0 and digest_int <= block_target_int

    return PowTargetDecision(
        digest_int=digest_int,
        digest_hex=digest_hex,
        theta_micro=theta,
        share_ratio=ratio,
        share_threshold_micro=share_threshold_micro,
        share_target_int=share_target_int,
        block_target_int=block_target_int,
        share_ok=share_ok,
        is_block=is_block,
    )

