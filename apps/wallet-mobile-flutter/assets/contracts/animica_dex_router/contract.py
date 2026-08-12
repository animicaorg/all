from __future__ import annotations

from stdlib import abi, events, treasury, storage

K_INIT = b"dexrouter:init"
K_OWNER = b"dexrouter:owner"
K_FACTORY = b"dexrouter:factory"


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


def _ensure_init() -> None:
    abi.require(_uget(K_INIT) == 1, b"not_initialized")


def _ensure_owner() -> None:
    abi.require(abi.caller() == _bget(K_OWNER), b"not_owner")


def _factory() -> bytes:
    return _bget(K_FACTORY)


def _pair_for(token_a: bytes, token_b: bytes) -> bytes:
    pair = abi.call_contract(_factory(), "get_pair", [bytes(token_a), bytes(token_b)], read_only=True)
    pair_b = bytes(pair)
    abi.require(pair_b != b"", b"pair_not_found")
    return pair_b


def _pair_token0(pair: bytes) -> bytes:
    return bytes(abi.call_contract(pair, "token0", [], read_only=True))


def _expected_native_value_for_tokens(token_a: bytes, token_b: bytes, amount_a: int, amount_b: int) -> int:
    a_native = bytes(token_a) == b""
    b_native = bytes(token_b) == b""
    abi.require(not (a_native and b_native), b"both_native_not_supported")

    if a_native:
        return int(amount_a)
    if b_native:
        return int(amount_b)
    return 0


def _enforce_native_value(expected: int) -> None:
    abi.require(int(abi.value()) == int(expected), b"native_value_mismatch")


def init(owner: bytes, factory: bytes) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")

    o = bytes(owner)
    f = bytes(factory)
    abi.require(len(o) > 0, b"bad_owner")
    abi.require(len(f) > 0, b"bad_factory")

    _bset(K_OWNER, o)
    _bset(K_FACTORY, f)
    _uset(K_INIT, 1)


def owner() -> bytes:
    _ensure_init()
    return _bget(K_OWNER)


def factory() -> bytes:
    _ensure_init()
    return _factory()


def set_owner(new_owner: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    n = bytes(new_owner)
    abi.require(len(n) > 0, b"bad_owner")
    prev = _bget(K_OWNER)
    _bset(K_OWNER, n)
    events.emit(b"OwnershipTransferred", {"previousOwner": prev, "newOwner": n})


def set_factory(new_factory: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    f = bytes(new_factory)
    abi.require(len(f) > 0, b"bad_factory")
    prev = _bget(K_FACTORY)
    _bset(K_FACTORY, f)
    events.emit(b"FactoryUpdated", {"previousFactory": prev, "newFactory": f})


def get_pair(token_a: bytes, token_b: bytes) -> bytes:
    _ensure_init()
    return _pair_for(token_a, token_b)


def create_pair(pair_address: bytes, token_a: bytes, token_b: bytes, fee_bps: int, metadata_uri: bytes) -> bytes:
    _ensure_init()

    p = bytes(pair_address)
    abi.require(len(p) > 0, b"bad_pair")

    launch_fee = int(abi.call_contract(_factory(), "launch_fee_anm", [], read_only=True))
    recipient = bytes(abi.call_contract(_factory(), "fee_recipient", [], read_only=True))

    _enforce_native_value(launch_fee)
    if launch_fee > 0:
        abi.require(recipient != b"", b"bad_fee_recipient")
        treasury.transfer(recipient, launch_fee)

    created = abi.call_contract(
        _factory(),
        "register_pair",
        [bytes(token_a), bytes(token_b), p, abi.caller(), int(fee_bps), bytes(metadata_uri)],
    )
    pair_out = bytes(created)

    events.emit(
        b"PairCreateRequested",
        {
            "creator": abi.caller(),
            "pair": pair_out,
            "tokenA": bytes(token_a),
            "tokenB": bytes(token_b),
            "feeBps": int(fee_bps),
            "metadataUri": bytes(metadata_uri),
        },
    )
    return pair_out


def quote_exact_in(token_in: bytes, token_out: bytes, amount_in: int) -> int:
    _ensure_init()
    pair = _pair_for(token_in, token_out)
    return int(abi.call_contract(pair, "quote_out", [bytes(token_in), int(amount_in)], read_only=True))


def quote_exact_out(token_in: bytes, token_out: bytes, amount_out: int) -> int:
    _ensure_init()
    pair = _pair_for(token_in, token_out)
    _ = bytes(token_in)
    return int(abi.call_contract(pair, "quote_in", [bytes(token_out), int(amount_out)], read_only=True))


def add_liquidity(
    token_a: bytes,
    token_b: bytes,
    amount_a_desired: int,
    amount_b_desired: int,
    min_lp: int,
    deadline: int,
) -> int:
    _ensure_init()

    a = int(amount_a_desired)
    b = int(amount_b_desired)
    abi.require(a > 0 and b > 0, b"amount_zero")

    pair = _pair_for(token_a, token_b)
    expected_native = _expected_native_value_for_tokens(token_a, token_b, a, b)
    _enforce_native_value(expected_native)
    caller = abi.caller()

    t0 = _pair_token0(pair)
    if bytes(token_a) == t0:
        minted = abi.call_contract(
            pair,
            "add_liquidity",
            [a, b, int(min_lp), caller, caller, int(deadline)],
            int(abi.value()),
        )
    else:
        minted = abi.call_contract(
            pair,
            "add_liquidity",
            [b, a, int(min_lp), caller, caller, int(deadline)],
            int(abi.value()),
        )

    minted_int = int(minted)
    events.emit(
        b"RouterLiquidityAdded",
        {
            "provider": abi.caller(),
            "pair": pair,
            "tokenA": bytes(token_a),
            "tokenB": bytes(token_b),
            "amountA": a,
            "amountB": b,
            "lpMinted": minted_int,
        },
    )
    return minted_int


def remove_liquidity(
    token_a: bytes,
    token_b: bytes,
    lp_amount: int,
    min_amount_a: int,
    min_amount_b: int,
    deadline: int,
) -> tuple:
    _ensure_init()

    lp = int(lp_amount)
    abi.require(lp > 0, b"lp_zero")

    pair = _pair_for(token_a, token_b)
    t0 = _pair_token0(pair)
    caller = abi.caller()

    if bytes(token_a) == t0:
        min0 = int(min_amount_a)
        min1 = int(min_amount_b)
    else:
        min0 = int(min_amount_b)
        min1 = int(min_amount_a)

    result = abi.call_contract(pair, "remove_liquidity", [lp, min0, min1, caller, caller, int(deadline)])
    amount0 = int(result[0])
    amount1 = int(result[1])

    if bytes(token_a) == t0:
        amount_a = amount0
        amount_b = amount1
    else:
        amount_a = amount1
        amount_b = amount0

    events.emit(
        b"RouterLiquidityRemoved",
        {
            "provider": abi.caller(),
            "pair": pair,
            "tokenA": bytes(token_a),
            "tokenB": bytes(token_b),
            "amountA": amount_a,
            "amountB": amount_b,
            "lpBurned": lp,
        },
    )
    return (amount_a, amount_b)


def swap_exact_in(
    token_in: bytes,
    token_out: bytes,
    amount_in: int,
    min_amount_out: int,
    to: bytes,
    deadline: int,
) -> int:
    _ensure_init()

    tin = bytes(token_in)
    tout = bytes(token_out)
    abi.require(tin != tout, b"identical_tokens")

    amt_in = int(amount_in)
    abi.require(amt_in > 0, b"amount_zero")

    expected_native = amt_in if tin == b"" else 0
    _enforce_native_value(expected_native)

    pair = _pair_for(tin, tout)
    out = abi.call_contract(
        pair,
        "swap_exact_in",
        [tin, amt_in, int(min_amount_out), bytes(to), abi.caller(), int(deadline)],
        int(abi.value()),
    )
    out_i = int(out)

    events.emit(
        b"RouterSwap",
        {
            "sender": abi.caller(),
            "pair": pair,
            "tokenIn": tin,
            "tokenOut": tout,
            "amountIn": amt_in,
            "amountOut": out_i,
            "to": bytes(to),
        },
    )
    return out_i


def swap_exact_out(
    token_in: bytes,
    token_out: bytes,
    amount_out: int,
    max_amount_in: int,
    to: bytes,
    deadline: int,
) -> int:
    _ensure_init()

    tin = bytes(token_in)
    tout = bytes(token_out)
    abi.require(tin != tout, b"identical_tokens")

    required_in = quote_exact_out(tin, tout, int(amount_out))
    abi.require(required_in <= int(max_amount_in), b"slippage_in")

    expected_native = required_in if tin == b"" else 0
    _enforce_native_value(expected_native)

    pair = _pair_for(tin, tout)
    used_in = abi.call_contract(
        pair,
        "swap_exact_out",
        [tout, int(amount_out), int(max_amount_in), bytes(to), abi.caller(), int(deadline)],
        int(abi.value()),
    )

    used = int(used_in)
    events.emit(
        b"RouterSwap",
        {
            "sender": abi.caller(),
            "pair": pair,
            "tokenIn": tin,
            "tokenOut": tout,
            "amountIn": used,
            "amountOut": int(amount_out),
            "to": bytes(to),
        },
    )
    return used
