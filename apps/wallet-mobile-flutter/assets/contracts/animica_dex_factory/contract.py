from __future__ import annotations

from stdlib import abi, events, hash, storage

K_INIT = b"dexfac:init"
K_OWNER = b"dexfac:owner"
K_ROUTER = b"dexfac:router"
K_DEFAULT_FEE_BPS = b"dexfac:default_fee_bps"
K_LAUNCH_FEE = b"dexfac:launch_fee_anm"
K_FEE_RECIPIENT = b"dexfac:fee_recipient"
K_PAIR_COUNT = b"dexfac:pair_count"


def _k_pair(token0: bytes, token1: bytes) -> bytes:
    return b"dexfac:pair:" + token0 + b":" + token1


def _k_pair_at(index: int) -> bytes:
    return b"dexfac:pair_at:" + int(index).to_bytes(8, "big")


def _k_pair_meta(pair: bytes) -> bytes:
    return b"dexfac:pair_meta:" + pair


def _k_pair_fee(pair: bytes) -> bytes:
    return b"dexfac:pair_fee:" + pair


def _k_pair_creator(pair: bytes) -> bytes:
    return b"dexfac:pair_creator:" + pair


def _k_pair_id(pair: bytes) -> bytes:
    return b"dexfac:pair_id:" + pair


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


def _ensure_owner_or_router() -> None:
    caller = abi.caller()
    abi.require(caller == _bget(K_OWNER) or caller == _bget(K_ROUTER), b"not_authorized")


def _sort_tokens(token_a: bytes, token_b: bytes) -> tuple[bytes, bytes]:
    a = bytes(token_a)
    b = bytes(token_b)
    abi.require(a != b, b"identical_tokens")
    abi.require(not (a == b"" and b == b""), b"both_native_not_supported")
    if b < a:
        return b, a
    return a, b


def _resolve_pair_id(token0: bytes, token1: bytes, pair: bytes) -> bytes:
    return hash.sha3_256(b"animica:dex:pair:v1|" + token0 + b"|" + token1 + b"|" + pair)


def init(owner: bytes, router: bytes, default_fee_bps: int, launch_fee_anm: int, fee_recipient: bytes) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")

    o = bytes(owner)
    r = bytes(router)
    fr = bytes(fee_recipient)
    fee = int(default_fee_bps)
    launch_fee = int(launch_fee_anm)

    abi.require(len(o) > 0, b"bad_owner")
    abi.require(len(r) > 0, b"bad_router")
    abi.require(fee >= 1 and fee <= 300, b"bad_fee_bps")
    abi.require(launch_fee >= 0, b"bad_launch_fee")

    _bset(K_OWNER, o)
    _bset(K_ROUTER, r)
    _uset(K_DEFAULT_FEE_BPS, fee)
    _uset(K_LAUNCH_FEE, launch_fee)
    if fr == b"":
        _bset(K_FEE_RECIPIENT, o)
    else:
        _bset(K_FEE_RECIPIENT, fr)
    _uset(K_INIT, 1)


def owner() -> bytes:
    _ensure_init()
    return _bget(K_OWNER)


def router() -> bytes:
    _ensure_init()
    return _bget(K_ROUTER)


def default_fee_bps() -> int:
    _ensure_init()
    return _uget(K_DEFAULT_FEE_BPS)


def launch_fee_anm() -> int:
    _ensure_init()
    return _uget(K_LAUNCH_FEE)


def fee_recipient() -> bytes:
    _ensure_init()
    return _bget(K_FEE_RECIPIENT)


def pair_count() -> int:
    _ensure_init()
    return _uget(K_PAIR_COUNT)


def get_pair(token_a: bytes, token_b: bytes) -> bytes:
    _ensure_init()
    t0, t1 = _sort_tokens(token_a, token_b)
    return _bget(_k_pair(t0, t1))


def pair_at(index: int) -> bytes:
    _ensure_init()
    i = int(index)
    abi.require(i >= 0 and i < _uget(K_PAIR_COUNT), b"index_oob")
    return _bget(_k_pair_at(i))


def pair_metadata(pair: bytes) -> bytes:
    _ensure_init()
    return _bget(_k_pair_meta(bytes(pair)))


def pair_fee_bps(pair: bytes) -> int:
    _ensure_init()
    p = bytes(pair)
    v = _uget(_k_pair_fee(p))
    if v == 0:
        return _uget(K_DEFAULT_FEE_BPS)
    return v


def pair_creator(pair: bytes) -> bytes:
    _ensure_init()
    return _bget(_k_pair_creator(bytes(pair)))


def pair_id(pair: bytes) -> bytes:
    _ensure_init()
    return _bget(_k_pair_id(bytes(pair)))


def set_owner(new_owner: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    n = bytes(new_owner)
    abi.require(len(n) > 0, b"bad_owner")
    prev = _bget(K_OWNER)
    _bset(K_OWNER, n)
    events.emit(b"OwnershipTransferred", {"previousOwner": prev, "newOwner": n})


def set_router(new_router: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    r = bytes(new_router)
    abi.require(len(r) > 0, b"bad_router")
    prev = _bget(K_ROUTER)
    _bset(K_ROUTER, r)
    events.emit(b"RouterUpdated", {"previousRouter": prev, "newRouter": r})


def set_default_fee_bps(new_fee_bps: int) -> None:
    _ensure_init()
    _ensure_owner()
    fee = int(new_fee_bps)
    abi.require(fee >= 1 and fee <= 300, b"bad_fee_bps")
    _uset(K_DEFAULT_FEE_BPS, fee)
    events.emit(b"DefaultFeeUpdated", {"feeBps": fee})


def set_launch_fee_anm(new_fee: int) -> None:
    _ensure_init()
    _ensure_owner()
    f = int(new_fee)
    abi.require(f >= 0, b"bad_launch_fee")
    _uset(K_LAUNCH_FEE, f)
    events.emit(b"LaunchFeeUpdated", {"launchFee": f})


def set_fee_recipient(new_recipient: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    r = bytes(new_recipient)
    abi.require(len(r) > 0, b"bad_fee_recipient")
    _bset(K_FEE_RECIPIENT, r)
    events.emit(b"FeeRecipientUpdated", {"recipient": r})


def set_pair_metadata(pair: bytes, metadata_uri: bytes) -> None:
    _ensure_init()
    _ensure_owner_or_router()
    p = bytes(pair)
    abi.require(len(p) > 0, b"bad_pair")
    _bset(_k_pair_meta(p), bytes(metadata_uri))
    events.emit(b"PairMetadataUpdated", {"pair": p, "metadataUri": bytes(metadata_uri)})


def register_pair(
    token_a: bytes,
    token_b: bytes,
    pair: bytes,
    creator: bytes,
    fee_bps: int,
    metadata_uri: bytes,
) -> bytes:
    _ensure_init()
    _ensure_owner_or_router()

    p = bytes(pair)
    abi.require(len(p) > 0, b"bad_pair")

    t0, t1 = _sort_tokens(token_a, token_b)
    key = _k_pair(t0, t1)
    abi.require(_bget(key) == b"", b"pair_exists")

    configured_fee = int(fee_bps)
    if configured_fee <= 0:
        configured_fee = _uget(K_DEFAULT_FEE_BPS)
    abi.require(configured_fee >= 1 and configured_fee <= 300, b"bad_fee_bps")

    abi.call_contract(p, "init", [t0, t1, configured_fee, _bget(K_OWNER)])

    _bset(key, p)

    idx = _uget(K_PAIR_COUNT)
    _bset(_k_pair_at(idx), p)
    _uset(K_PAIR_COUNT, idx + 1)

    _uset(_k_pair_fee(p), configured_fee)
    _bset(_k_pair_creator(p), bytes(creator))
    _bset(_k_pair_meta(p), bytes(metadata_uri))

    pid = _resolve_pair_id(t0, t1, p)
    _bset(_k_pair_id(p), pid)

    events.emit(
        b"PairCreated",
        {
            "pair": p,
            "token0": t0,
            "token1": t1,
            "feeBps": configured_fee,
            "creator": bytes(creator),
            "pairId": pid,
            "metadataUri": bytes(metadata_uri),
        },
    )
    return p
