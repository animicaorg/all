from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from web3 import Web3

BANM_WEI_UNIT = 10**18
ANM_TO_BANM_SCALER = 10 ** (18 - 9)


def validate_evm_address(address: str) -> str:
    candidate = (address or "").strip()
    if not candidate or not Web3.is_address(candidate):
        raise ValueError("invalid EVM address")
    return Web3.to_checksum_address(candidate)


def validate_animica_address(address: str) -> str:
    candidate = (address or "").strip()
    if not candidate:
        raise ValueError("Animica address is required")
    try:
        from pq.py.address import validate_address  # type: ignore

        validate_address(candidate, expect_hrp="anim")
        return candidate
    except Exception:
        pass

    try:
        from omni_sdk.address import validate  # type: ignore

        if validate(candidate, expected_hrp="anim"):
            return candidate
    except Exception:
        pass

    # Dev/test fallback when native validators are unavailable.
    if candidate.startswith("anim1") and len(candidate) >= 16:
        return candidate
    raise ValueError("invalid Animica address")


def parse_human_amount(amount_text: str, decimals: int) -> int:
    try:
        dec = Decimal(amount_text.strip())
    except Exception as exc:  # noqa: BLE001
        raise ValueError("amount must be a numeric string") from exc
    if dec <= 0:
        raise ValueError("amount must be > 0")
    scaled = (dec * (Decimal(10) ** decimals)).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return int(scaled)


def anm_base_to_banm_wei(anm_base_units: int) -> int:
    return int(anm_base_units) * ANM_TO_BANM_SCALER


def banm_wei_to_anm_base(banm_wei: int) -> int:
    return int(banm_wei) // ANM_TO_BANM_SCALER


def apply_fee(amount: int, fee_bps: int) -> tuple[int, int]:
    fee = (int(amount) * int(fee_bps)) // 10_000
    return int(amount) - fee, fee


def format_amount_for_ui(amount_base: int, decimals: int) -> str:
    quant = Decimal(amount_base) / (Decimal(10) ** decimals)
    return f"{quant.normalize():f}"


def anm_base_to_human(amount_base: int) -> str:
    return format_amount_for_ui(amount_base, 9)


def banm_wei_to_human(amount_wei: int) -> str:
    return format_amount_for_ui(amount_wei, 18)


def clamp_order_amount(
    amount: int,
    min_amount: int,
    max_amount: int,
    *,
    field_name: str,
) -> None:
    if amount < min_amount:
        raise ValueError(f"{field_name} below minimum")
    if amount > max_amount:
        raise ValueError(f"{field_name} above maximum")


def to_anm_base_units_from_human(amount_text: str) -> int:
    return parse_human_amount(amount_text, 9)


def to_banm_wei_from_human(amount_text: str) -> int:
    # BANM UI amounts intentionally follow Animica precision (9 decimals).
    return parse_human_amount(amount_text, 9) * ANM_TO_BANM_SCALER
