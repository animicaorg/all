from __future__ import annotations

from stdlib import abi, events, hash, pq_verify, storage

K_INIT = b"usdan:mint:init"
K_OWNER = b"usdan:mint:owner"
K_TOKEN = b"usdan:mint:token"

K_MIN_VALIDITY_BLOCKS = b"usdan:mint:min_validity_blocks"
K_MAX_VALIDITY_BLOCKS = b"usdan:mint:max_validity_blocks"


def _k_operator(account: bytes) -> bytes:
    return b"usdan:mint:operator:" + bytes(account)


def _k_signer(pubkey_hash: bytes) -> bytes:
    return b"usdan:mint:signer:" + bytes(pubkey_hash)


def _k_nonce(nonce: bytes) -> bytes:
    return b"usdan:mint:nonce:" + bytes(nonce)


def _k_request(req_id: bytes) -> bytes:
    return b"usdan:mint:request:" + bytes(req_id)


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


def _ensure_nonzero_addr(addr: bytes, err: bytes) -> None:
    abi.require(isinstance(addr, (bytes, bytearray)), err)
    abi.require(len(addr) > 0, err)


def _owner() -> bytes:
    return _bget(K_OWNER)


def _token() -> bytes:
    return _bget(K_TOKEN)


def _ensure_owner() -> None:
    abi.require(bytes(abi.caller()) == _owner(), b"not_owner")


def _ensure_operator() -> None:
    caller = bytes(abi.caller())
    abi.require(_uget(_k_operator(caller)) == 1, b"not_operator")


def _signer_allowed(pubkey: bytes) -> bool:
    return _uget(_k_signer(hash.sha3_256(bytes(pubkey)))) == 1


def _mint_message(
    recipient: bytes,
    amount: int,
    fiat_reference: bytes,
    request_id: bytes,
    nonce: bytes,
    valid_after_block: int,
    valid_before_block: int,
) -> bytes:
    payload = (
        b"USDAN_MINT|"
        + str(int(abi.chain_id())).encode("utf-8")
        + b"|"
        + bytes(abi.contract_address())
        + b"|"
        + _token()
        + b"|"
        + bytes(recipient)
        + b"|"
        + int(amount).to_bytes(32, "big")
        + b"|"
        + bytes(fiat_reference)
        + b"|"
        + bytes(request_id)
        + b"|"
        + bytes(nonce)
        + b"|"
        + int(valid_after_block).to_bytes(8, "big")
        + b"|"
        + int(valid_before_block).to_bytes(8, "big")
    )
    return hash.sha3_256(payload)


def init(
    owner: bytes,
    token: bytes,
    min_validity_blocks: int,
    max_validity_blocks: int,
) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")
    _ensure_nonzero_addr(owner, b"bad_owner")
    _ensure_nonzero_addr(token, b"bad_token")

    min_blocks = int(min_validity_blocks)
    max_blocks = int(max_validity_blocks)
    abi.require(min_blocks >= 0, b"bad_min_validity")
    abi.require(max_blocks >= min_blocks, b"bad_max_validity")

    _bset(K_OWNER, bytes(owner))
    _bset(K_TOKEN, bytes(token))
    _uset(K_MIN_VALIDITY_BLOCKS, min_blocks)
    _uset(K_MAX_VALIDITY_BLOCKS, max_blocks)
    _uset(_k_operator(bytes(owner)), 1)

    _uset(K_INIT, 1)


def owner() -> bytes:
    _ensure_init()
    return _owner()


def token() -> bytes:
    _ensure_init()
    return _token()


def is_operator(account: bytes) -> bool:
    _ensure_init()
    return _uget(_k_operator(bytes(account))) == 1


def is_signer(pubkey_hash: bytes) -> bool:
    _ensure_init()
    return _uget(_k_signer(bytes(pubkey_hash))) == 1


def nonce_used(nonce: bytes) -> bool:
    _ensure_init()
    return _uget(_k_nonce(bytes(nonce))) == 1


def request_consumed(request_id: bytes) -> bool:
    _ensure_init()
    return _uget(_k_request(bytes(request_id))) == 1


def min_validity_blocks() -> int:
    _ensure_init()
    return _uget(K_MIN_VALIDITY_BLOCKS)


def max_validity_blocks() -> int:
    _ensure_init()
    return _uget(K_MAX_VALIDITY_BLOCKS)


def mint_message(
    recipient: bytes,
    amount: int,
    fiat_reference: bytes,
    request_id: bytes,
    nonce: bytes,
    valid_after_block: int,
    valid_before_block: int,
) -> bytes:
    _ensure_init()
    return _mint_message(
        bytes(recipient),
        int(amount),
        bytes(fiat_reference),
        bytes(request_id),
        bytes(nonce),
        int(valid_after_block),
        int(valid_before_block),
    )


def set_owner(new_owner: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    n = bytes(new_owner)
    _ensure_nonzero_addr(n, b"bad_owner")
    prev = _owner()
    _bset(K_OWNER, n)
    _uset(_k_operator(n), 1)
    events.emit(b"OwnershipTransferred", {"previousOwner": prev, "newOwner": n})


def set_token(new_token: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    t = bytes(new_token)
    _ensure_nonzero_addr(t, b"bad_token")
    prev = _token()
    _bset(K_TOKEN, t)
    events.emit(b"TokenUpdated", {"previousToken": prev, "newToken": t})


def set_operator(account: bytes, enabled: bool) -> None:
    _ensure_init()
    _ensure_owner()
    a = bytes(account)
    _ensure_nonzero_addr(a, b"bad_account")
    _uset(_k_operator(a), 1 if bool(enabled) else 0)
    events.emit(b"OperatorUpdated", {"account": a, "enabled": bool(enabled)})


def set_signer(pubkey_hash: bytes, enabled: bool) -> None:
    _ensure_init()
    _ensure_owner()
    p = bytes(pubkey_hash)
    abi.require(len(p) == 32, b"bad_signer_hash")
    _uset(_k_signer(p), 1 if bool(enabled) else 0)
    events.emit(b"SignerUpdated", {"pubkeyHash": p, "enabled": bool(enabled)})


def set_validity_windows(min_validity_blocks: int, max_validity_blocks: int) -> None:
    _ensure_init()
    _ensure_owner()
    min_blocks = int(min_validity_blocks)
    max_blocks = int(max_validity_blocks)
    abi.require(min_blocks >= 0, b"bad_min_validity")
    abi.require(max_blocks >= min_blocks, b"bad_max_validity")

    _uset(K_MIN_VALIDITY_BLOCKS, min_blocks)
    _uset(K_MAX_VALIDITY_BLOCKS, max_blocks)
    events.emit(
        b"ValidityWindowUpdated",
        {"minValidityBlocks": min_blocks, "maxValidityBlocks": max_blocks},
    )


def execute_mint(
    recipient: bytes,
    amount: int,
    fiat_reference: bytes,
    request_id: bytes,
    nonce: bytes,
    valid_after_block: int,
    valid_before_block: int,
    signer_pubkey: bytes,
    signature: bytes,
) -> bool:
    _ensure_init()
    _ensure_operator()

    dst = bytes(recipient)
    req_id = bytes(request_id)
    n = bytes(nonce)
    signer = bytes(signer_pubkey)
    sig = bytes(signature)
    amt = int(amount)

    _ensure_nonzero_addr(dst, b"bad_recipient")
    abi.require(amt > 0, b"amount_zero")
    abi.require(len(req_id) > 0, b"bad_request_id")
    abi.require(len(n) > 0, b"bad_nonce")
    abi.require(len(sig) > 0, b"bad_signature")

    abi.require(not nonce_used(n), b"nonce_used")
    abi.require(not request_consumed(req_id), b"request_used")
    abi.require(_signer_allowed(signer), b"signer_not_allowed")

    now_block = int(abi.block_height())
    start_block = int(valid_after_block)
    end_block = int(valid_before_block)
    abi.require(end_block >= start_block, b"bad_validity_range")
    abi.require(now_block >= start_block, b"mint_not_yet_valid")
    abi.require(now_block <= end_block, b"mint_expired")

    span = end_block - start_block
    abi.require(span >= _uget(K_MIN_VALIDITY_BLOCKS), b"validity_too_short")
    abi.require(span <= _uget(K_MAX_VALIDITY_BLOCKS), b"validity_too_long")

    message = _mint_message(dst, amt, bytes(fiat_reference), req_id, n, start_block, end_block)
    abi.require(pq_verify.verify(signer, message, sig), b"bad_signature")

    events.emit(
        b"MintRequestAccepted",
        {
            "operator": bytes(abi.caller()),
            "recipient": dst,
            "amount": amt,
            "requestId": req_id,
            "nonce": n,
            "fiatReference": bytes(fiat_reference),
        },
    )

    abi.call_contract(_token(), "mint", [dst, amt])

    _uset(_k_nonce(n), 1)
    _uset(_k_request(req_id), 1)

    events.emit(
        b"MintCompleted",
        {
            "recipient": dst,
            "amount": amt,
            "requestId": req_id,
            "nonce": n,
            "executedBy": bytes(abi.caller()),
            "signerHash": hash.sha3_256(signer),
        },
    )
    return True
