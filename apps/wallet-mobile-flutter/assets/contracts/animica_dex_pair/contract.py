from __future__ import annotations

from stdlib import abi, events, storage, treasury

K_INIT = b"dexpair:init"
K_TOKEN0 = b"dexpair:token0"
K_TOKEN1 = b"dexpair:token1"
K_FEE_BPS = b"dexpair:fee_bps"
K_OWNER = b"dexpair:owner"
K_RES0 = b"dexpair:reserve0"
K_RES1 = b"dexpair:reserve1"
K_LP_TOTAL = b"dexpair:lp_total"


def _k_lp(addr: bytes) -> bytes:
    return b"dexpair:lp:" + addr


def _uget(key: bytes) -> int:
    raw = storage.get(key, b"")
    if raw == b"":
        return 0
    return int.from_bytes(raw, "big")


def _uset(key: bytes, value: int) -> None:
    v = int(value)
    abi.require(v >= 0, b"negative")
    if v == 0:
        storage.delete(key)
        return
    storage.set(key, v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big"))


def _bget(key: bytes) -> bytes:
    return storage.get(key, b"")


def _bset(key: bytes, value: bytes) -> None:
    storage.set(key, bytes(value))


def _is_native(token: bytes) -> bool:
    return token == b""


def _ensure_init() -> None:
    abi.require(_uget(K_INIT) == 1, b"not_initialized")


def _ensure_owner() -> None:
    abi.require(abi.caller() == _bget(K_OWNER), b"not_owner")


def _now_height() -> int:
    return int(abi.block_height())


def _check_deadline(deadline: int) -> None:
    d = int(deadline)
    if d == 0:
        return
    abi.require(_now_height() <= d, b"deadline_expired")


def _min(a: int, b: int) -> int:
    if a < b:
        return a
    return b


def _sqrt(y: int) -> int:
    x = int(y)
    if x <= 0:
        return 0
    z = x
    t = (x // 2) + 1
    while t < z:
        z = t
        t = (x // t + t) // 2
    return z


def _token_transfer_from(token: bytes, owner: bytes, amount: int) -> None:
    if _is_native(token):
        return
    abi.call_contract(token, "transfer_from", [owner, abi.contract_address(), int(amount)])


def _token_transfer_to(token: bytes, to: bytes, amount: int) -> None:
    amt = int(amount)
    if amt == 0:
        return
    if _is_native(token):
        treasury.transfer(to, amt)
        return
    abi.call_contract(token, "transfer", [to, amt])


def _require_valid_tokens(token0: bytes, token1: bytes) -> None:
    abi.require(token0 != token1, b"identical_tokens")
    abi.require(not (_is_native(token0) and _is_native(token1)), b"both_native_not_supported")


def _validate_native_sent(token0: bytes, token1: bytes, amount0: int, amount1: int) -> None:
    sent = int(abi.value())
    if _is_native(token0):
        abi.require(sent == int(amount0), b"native_amount_mismatch")
        return
    if _is_native(token1):
        abi.require(sent == int(amount1), b"native_amount_mismatch")
        return
    abi.require(sent == 0, b"unexpected_native_value")


def _mint_lp(to: bytes, amount: int) -> None:
    amt = int(amount)
    abi.require(amt > 0, b"lp_zero")
    _uset(K_LP_TOTAL, _uget(K_LP_TOTAL) + amt)
    _uset(_k_lp(to), _uget(_k_lp(to)) + amt)


def _burn_lp(from_addr: bytes, amount: int) -> None:
    amt = int(amount)
    abi.require(amt > 0, b"lp_zero")
    bal = _uget(_k_lp(from_addr))
    abi.require(bal >= amt, b"lp_insufficient")
    _uset(_k_lp(from_addr), bal - amt)
    _uset(K_LP_TOTAL, _uget(K_LP_TOTAL) - amt)


def _get_amount_out(amount_in: int, reserve_in: int, reserve_out: int, fee_bps: int) -> int:
    abi.require(amount_in > 0, b"amount_zero")
    abi.require(reserve_in > 0 and reserve_out > 0, b"no_liquidity")
    fee_factor = 10_000 - int(fee_bps)
    amount_in_with_fee = int(amount_in) * fee_factor
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * 10_000 + amount_in_with_fee
    abi.require(denominator > 0, b"denominator_zero")
    return numerator // denominator


def _get_amount_in(amount_out: int, reserve_in: int, reserve_out: int, fee_bps: int) -> int:
    abi.require(amount_out > 0, b"amount_zero")
    abi.require(reserve_in > 0 and reserve_out > 0, b"no_liquidity")
    abi.require(amount_out < reserve_out, b"insufficient_liquidity")
    fee_factor = 10_000 - int(fee_bps)
    numerator = reserve_in * amount_out * 10_000
    denominator = (reserve_out - amount_out) * fee_factor
    abi.require(denominator > 0, b"denominator_zero")
    return (numerator // denominator) + 1


def init(token0: bytes, token1: bytes, fee_bps: int, owner: bytes) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")

    t0 = bytes(token0)
    t1 = bytes(token1)
    if t1 < t0:
        tmp = t0
        t0 = t1
        t1 = tmp
    _require_valid_tokens(t0, t1)

    fee = int(fee_bps)
    abi.require(fee >= 1 and fee <= 300, b"bad_fee_bps")

    o = bytes(owner)
    abi.require(len(o) > 0, b"bad_owner")

    _bset(K_TOKEN0, t0)
    _bset(K_TOKEN1, t1)
    _uset(K_FEE_BPS, fee)
    _bset(K_OWNER, o)
    _uset(K_INIT, 1)


def token0() -> bytes:
    _ensure_init()
    return _bget(K_TOKEN0)


def token1() -> bytes:
    _ensure_init()
    return _bget(K_TOKEN1)


def fee_bps() -> int:
    _ensure_init()
    return _uget(K_FEE_BPS)


def owner() -> bytes:
    _ensure_init()
    return _bget(K_OWNER)


def reserves() -> tuple:
    _ensure_init()
    return (_uget(K_RES0), _uget(K_RES1))


def lp_total_supply() -> int:
    _ensure_init()
    return _uget(K_LP_TOTAL)


def lp_balance_of(addr: bytes) -> int:
    _ensure_init()
    return _uget(_k_lp(addr))


def pair_tokens() -> tuple:
    _ensure_init()
    return (_bget(K_TOKEN0), _bget(K_TOKEN1))


def quote_out(token_in: bytes, amount_in: int) -> int:
    _ensure_init()
    tin = bytes(token_in)
    amt_in = int(amount_in)
    t0 = _bget(K_TOKEN0)
    t1 = _bget(K_TOKEN1)
    r0 = _uget(K_RES0)
    r1 = _uget(K_RES1)
    fee = _uget(K_FEE_BPS)

    if tin == t0:
        return _get_amount_out(amt_in, r0, r1, fee)
    if tin == t1:
        return _get_amount_out(amt_in, r1, r0, fee)
    abi.revert(b"invalid_token_in")
    return 0


def quote_in(token_out: bytes, amount_out: int) -> int:
    _ensure_init()
    tout = bytes(token_out)
    amt_out = int(amount_out)
    t0 = _bget(K_TOKEN0)
    t1 = _bget(K_TOKEN1)
    r0 = _uget(K_RES0)
    r1 = _uget(K_RES1)
    fee = _uget(K_FEE_BPS)

    if tout == t1:
        return _get_amount_in(amt_out, r0, r1, fee)
    if tout == t0:
        return _get_amount_in(amt_out, r1, r0, fee)
    abi.revert(b"invalid_token_out")
    return 0


def add_liquidity(
    amount0_desired: int,
    amount1_desired: int,
    min_lp: int,
    provider: bytes,
    payer: bytes,
    deadline: int,
) -> int:
    _ensure_init()
    _check_deadline(deadline)

    a0 = int(amount0_desired)
    a1 = int(amount1_desired)
    abi.require(a0 > 0 and a1 > 0, b"amount_zero")
    lp_provider = bytes(provider)
    token_payer = bytes(payer)
    abi.require(len(lp_provider) > 0, b"bad_provider")
    abi.require(len(token_payer) > 0, b"bad_payer")

    t0 = _bget(K_TOKEN0)
    t1 = _bget(K_TOKEN1)
    _validate_native_sent(t0, t1, a0, a1)

    if not _is_native(t0):
        _token_transfer_from(t0, token_payer, a0)
    if not _is_native(t1):
        _token_transfer_from(t1, token_payer, a1)

    r0 = _uget(K_RES0)
    r1 = _uget(K_RES1)
    total_lp = _uget(K_LP_TOTAL)

    if total_lp == 0:
        minted = _sqrt(a0 * a1)
    else:
        minted = _min((a0 * total_lp) // r0, (a1 * total_lp) // r1)

    abi.require(minted >= int(min_lp), b"slippage_lp")
    abi.require(minted > 0, b"lp_zero")

    _mint_lp(lp_provider, minted)
    _uset(K_RES0, r0 + a0)
    _uset(K_RES1, r1 + a1)

    events.emit(
        b"LiquidityAdded",
        {
            "provider": lp_provider,
            "amount0": a0,
            "amount1": a1,
            "lpMinted": minted,
        },
    )
    events.emit(b"Sync", {"reserve0": _uget(K_RES0), "reserve1": _uget(K_RES1)})
    return minted


def remove_liquidity(
    lp_amount: int,
    min_amount0: int,
    min_amount1: int,
    to: bytes,
    lp_owner: bytes,
    deadline: int,
) -> tuple:
    _ensure_init()
    _check_deadline(deadline)

    amt_lp = int(lp_amount)
    abi.require(amt_lp > 0, b"lp_zero")
    recipient = bytes(to)
    provider = bytes(lp_owner)
    abi.require(len(recipient) > 0, b"bad_to")
    abi.require(len(provider) > 0, b"bad_owner")

    total_lp = _uget(K_LP_TOTAL)
    abi.require(total_lp > 0, b"no_liquidity")

    r0 = _uget(K_RES0)
    r1 = _uget(K_RES1)

    amount0 = (amt_lp * r0) // total_lp
    amount1 = (amt_lp * r1) // total_lp

    abi.require(amount0 >= int(min_amount0), b"slippage0")
    abi.require(amount1 >= int(min_amount1), b"slippage1")

    _burn_lp(provider, amt_lp)
    _uset(K_RES0, r0 - amount0)
    _uset(K_RES1, r1 - amount1)

    _token_transfer_to(_bget(K_TOKEN0), recipient, amount0)
    _token_transfer_to(_bget(K_TOKEN1), recipient, amount1)

    events.emit(
        b"LiquidityRemoved",
        {
            "provider": provider,
            "amount0": amount0,
            "amount1": amount1,
            "lpBurned": amt_lp,
        },
    )
    events.emit(b"Sync", {"reserve0": _uget(K_RES0), "reserve1": _uget(K_RES1)})
    return (amount0, amount1)


def swap_exact_in(
    token_in: bytes,
    amount_in: int,
    min_amount_out: int,
    to: bytes,
    payer: bytes,
    deadline: int,
) -> int:
    _ensure_init()
    _check_deadline(deadline)

    tin = bytes(token_in)
    amt_in = int(amount_in)
    out_min = int(min_amount_out)
    recipient = bytes(to)
    trader = bytes(payer)

    abi.require(len(recipient) > 0, b"bad_to")
    abi.require(len(trader) > 0, b"bad_payer")
    abi.require(amt_in > 0, b"amount_zero")

    t0 = _bget(K_TOKEN0)
    t1 = _bget(K_TOKEN1)
    r0 = _uget(K_RES0)
    r1 = _uget(K_RES1)

    if tin == t0:
        if _is_native(t0):
            abi.require(int(abi.value()) == amt_in, b"native_amount_mismatch")
        else:
            abi.require(int(abi.value()) == 0, b"unexpected_native_value")
            _token_transfer_from(t0, trader, amt_in)

        out = _get_amount_out(amt_in, r0, r1, _uget(K_FEE_BPS))
        abi.require(out >= out_min, b"slippage_out")
        abi.require(out > 0 and out < r1, b"insufficient_out")

        _uset(K_RES0, r0 + amt_in)
        _uset(K_RES1, r1 - out)
        _token_transfer_to(t1, recipient, out)
    elif tin == t1:
        if _is_native(t1):
            abi.require(int(abi.value()) == amt_in, b"native_amount_mismatch")
        else:
            abi.require(int(abi.value()) == 0, b"unexpected_native_value")
            _token_transfer_from(t1, trader, amt_in)

        out = _get_amount_out(amt_in, r1, r0, _uget(K_FEE_BPS))
        abi.require(out >= out_min, b"slippage_out")
        abi.require(out > 0 and out < r0, b"insufficient_out")

        _uset(K_RES1, r1 + amt_in)
        _uset(K_RES0, r0 - out)
        _token_transfer_to(t0, recipient, out)
    else:
        abi.revert(b"invalid_token_in")
        return 0

    events.emit(
        b"Swap",
        {
            "sender": trader,
            "to": recipient,
            "tokenIn": tin,
            "amountIn": amt_in,
            "amountOut": out,
        },
    )
    events.emit(b"Sync", {"reserve0": _uget(K_RES0), "reserve1": _uget(K_RES1)})
    return out


def swap_exact_out(
    token_out: bytes,
    amount_out: int,
    max_amount_in: int,
    to: bytes,
    payer: bytes,
    deadline: int,
) -> int:
    _ensure_init()
    _check_deadline(deadline)

    tout = bytes(token_out)
    amt_out = int(amount_out)
    max_in = int(max_amount_in)
    recipient = bytes(to)
    trader = bytes(payer)

    abi.require(len(recipient) > 0, b"bad_to")
    abi.require(len(trader) > 0, b"bad_payer")
    abi.require(amt_out > 0, b"amount_zero")

    t0 = _bget(K_TOKEN0)
    t1 = _bget(K_TOKEN1)
    r0 = _uget(K_RES0)
    r1 = _uget(K_RES1)
    fee = _uget(K_FEE_BPS)

    if tout == t1:
        needed_in = _get_amount_in(amt_out, r0, r1, fee)
        abi.require(needed_in <= max_in, b"slippage_in")
        if _is_native(t0):
            abi.require(int(abi.value()) == needed_in, b"native_amount_mismatch")
        else:
            abi.require(int(abi.value()) == 0, b"unexpected_native_value")
            _token_transfer_from(t0, trader, needed_in)

        _uset(K_RES0, r0 + needed_in)
        _uset(K_RES1, r1 - amt_out)
        _token_transfer_to(t1, recipient, amt_out)
        token_in = t0
    elif tout == t0:
        needed_in = _get_amount_in(amt_out, r1, r0, fee)
        abi.require(needed_in <= max_in, b"slippage_in")
        if _is_native(t1):
            abi.require(int(abi.value()) == needed_in, b"native_amount_mismatch")
        else:
            abi.require(int(abi.value()) == 0, b"unexpected_native_value")
            _token_transfer_from(t1, trader, needed_in)

        _uset(K_RES1, r1 + needed_in)
        _uset(K_RES0, r0 - amt_out)
        _token_transfer_to(t0, recipient, amt_out)
        token_in = t1
    else:
        abi.revert(b"invalid_token_out")
        return 0

    events.emit(
        b"Swap",
        {
            "sender": trader,
            "to": recipient,
            "tokenIn": token_in,
            "amountIn": needed_in,
            "amountOut": amt_out,
        },
    )
    events.emit(b"Sync", {"reserve0": _uget(K_RES0), "reserve1": _uget(K_RES1)})
    return needed_in


def set_fee_bps(new_fee_bps: int) -> None:
    _ensure_init()
    _ensure_owner()
    fee = int(new_fee_bps)
    abi.require(fee >= 1 and fee <= 300, b"bad_fee_bps")
    _uset(K_FEE_BPS, fee)
    events.emit(b"FeeUpdated", {"feeBps": fee})


def set_owner(new_owner: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    o = bytes(new_owner)
    abi.require(len(o) > 0, b"bad_owner")
    prev = _bget(K_OWNER)
    _bset(K_OWNER, o)
    events.emit(b"OwnershipTransferred", {"previousOwner": prev, "newOwner": o})


def sync(reserve0: int, reserve1: int) -> None:
    _ensure_init()
    _ensure_owner()
    r0 = int(reserve0)
    r1 = int(reserve1)
    abi.require(r0 >= 0 and r1 >= 0, b"bad_reserve")
    _uset(K_RES0, r0)
    _uset(K_RES1, r1)
    events.emit(b"Sync", {"reserve0": r0, "reserve1": r1})
