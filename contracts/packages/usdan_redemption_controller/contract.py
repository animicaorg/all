from __future__ import annotations

from stdlib import abi, events, hash, pq_verify, storage

K_INIT = b"usdan:redeem:init"
K_OWNER = b"usdan:redeem:owner"
K_TOKEN = b"usdan:redeem:token"
K_ESCROW_MODE = b"usdan:redeem:escrow_mode"
K_REQ_SEQ = b"usdan:redeem:req_seq"


def _k_operator(account: bytes) -> bytes:
    return b"usdan:redeem:operator:" + bytes(account)


def _k_user_signer(user: bytes) -> bytes:
    return b"usdan:redeem:user_signer:" + bytes(user)


def _k_nonce(user: bytes, nonce: bytes) -> bytes:
    return b"usdan:redeem:nonce:" + bytes(user) + b":" + bytes(nonce)


def _k_req_exists(req_id: bytes) -> bytes:
    return b"usdan:redeem:req:exists:" + bytes(req_id)


def _k_req_user(req_id: bytes) -> bytes:
    return b"usdan:redeem:req:user:" + bytes(req_id)


def _k_req_amount(req_id: bytes) -> bytes:
    return b"usdan:redeem:req:amount:" + bytes(req_id)


def _k_req_bank(req_id: bytes) -> bytes:
    return b"usdan:redeem:req:bank:" + bytes(req_id)


def _k_req_status(req_id: bytes) -> bytes:
    return b"usdan:redeem:req:status:" + bytes(req_id)


def _k_req_created(req_id: bytes) -> bytes:
    return b"usdan:redeem:req:created:" + bytes(req_id)


def _k_req_updated(req_id: bytes) -> bytes:
    return b"usdan:redeem:req:updated:" + bytes(req_id)


def _k_req_cancel_reason(req_id: bytes) -> bytes:
    return b"usdan:redeem:req:cancel_reason:" + bytes(req_id)


def _k_req_payout_ref(req_id: bytes) -> bytes:
    return b"usdan:redeem:req:payout_ref:" + bytes(req_id)


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


def _is_operator(account: bytes) -> bool:
    return _uget(_k_operator(bytes(account))) == 1


def _ensure_owner() -> None:
    abi.require(bytes(abi.caller()) == _owner(), b"not_owner")


def _ensure_operator() -> None:
    caller = bytes(abi.caller())
    abi.require(_is_operator(caller), b"not_operator")


def _status_pending() -> int:
    return 1


def _status_cancelled() -> int:
    return 2


def _status_resolved() -> int:
    return 3


def _redemption_message(
    redeemer: bytes,
    amount: int,
    bank_account_ref: bytes,
    nonce: bytes,
    valid_before_block: int,
) -> bytes:
    payload = (
        b"USDAN_REDEEM|"
        + str(int(abi.chain_id())).encode("utf-8")
        + b"|"
        + bytes(abi.contract_address())
        + b"|"
        + _token()
        + b"|"
        + bytes(redeemer)
        + b"|"
        + int(amount).to_bytes(32, "big")
        + b"|"
        + bytes(bank_account_ref)
        + b"|"
        + bytes(nonce)
        + b"|"
        + int(valid_before_block).to_bytes(8, "big")
    )
    return hash.sha3_256(payload)


def init(owner: bytes, token: bytes, escrow_mode: bool) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")
    _ensure_nonzero_addr(owner, b"bad_owner")
    _ensure_nonzero_addr(token, b"bad_token")

    _bset(K_OWNER, bytes(owner))
    _bset(K_TOKEN, bytes(token))
    _uset(K_ESCROW_MODE, 1 if bool(escrow_mode) else 0)

    # Owner is always an operator.
    _uset(_k_operator(bytes(owner)), 1)
    _uset(K_INIT, 1)


def owner() -> bytes:
    _ensure_init()
    return _owner()


def token() -> bytes:
    _ensure_init()
    return _token()


def escrow_mode() -> bool:
    _ensure_init()
    return _uget(K_ESCROW_MODE) == 1


def is_operator(account: bytes) -> bool:
    _ensure_init()
    return _is_operator(bytes(account))


def user_signer_hash(user: bytes) -> bytes:
    _ensure_init()
    return _bget(_k_user_signer(bytes(user)))


def nonce_used(user: bytes, nonce: bytes) -> bool:
    _ensure_init()
    return _uget(_k_nonce(bytes(user), bytes(nonce))) == 1


def redemption_message(
    redeemer: bytes,
    amount: int,
    bank_account_ref: bytes,
    nonce: bytes,
    valid_before_block: int,
) -> bytes:
    _ensure_init()
    return _redemption_message(
        bytes(redeemer),
        int(amount),
        bytes(bank_account_ref),
        bytes(nonce),
        int(valid_before_block),
    )


def status_name(status_code: int) -> bytes:
    s = int(status_code)
    if s == _status_pending():
        return b"PENDING"
    if s == _status_cancelled():
        return b"CANCELLED"
    if s == _status_resolved():
        return b"RESOLVED"
    return b"UNKNOWN"


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


def set_user_signer(user: bytes, signer_pubkey_hash: bytes) -> None:
    _ensure_init()
    _ensure_operator()
    u = bytes(user)
    s = bytes(signer_pubkey_hash)
    _ensure_nonzero_addr(u, b"bad_user")
    abi.require(len(s) == 32, b"bad_signer_hash")
    _bset(_k_user_signer(u), s)
    events.emit(b"UserSignerUpdated", {"user": u, "signerHash": s})


def _next_request_id() -> bytes:
    seq = _uget(K_REQ_SEQ) + 1
    _uset(K_REQ_SEQ, seq)
    return seq.to_bytes(16, "big")


def initiate_redemption(
    amount: int,
    bank_account_ref: bytes,
    nonce: bytes,
    valid_before_block: int,
    signer_pubkey: bytes,
    signature: bytes,
) -> bytes:
    _ensure_init()

    caller = bytes(abi.caller())
    amt = int(amount)
    bank_ref = bytes(bank_account_ref)
    n = bytes(nonce)
    signer = bytes(signer_pubkey)
    sig = bytes(signature)

    _ensure_nonzero_addr(caller, b"bad_caller")
    abi.require(amt > 0, b"amount_zero")
    abi.require(len(bank_ref) > 0, b"bad_bank_ref")
    abi.require(len(n) > 0, b"bad_nonce")
    abi.require(len(sig) > 0, b"bad_signature")

    abi.require(not nonce_used(caller, n), b"nonce_used")

    expected_signer_hash = _bget(_k_user_signer(caller))
    abi.require(expected_signer_hash != b"", b"missing_user_signer")
    abi.require(hash.sha3_256(signer) == expected_signer_hash, b"unexpected_signer")

    now_block = int(abi.block_height())
    abi.require(now_block <= int(valid_before_block), b"intent_expired")

    msg = _redemption_message(caller, amt, bank_ref, n, int(valid_before_block))
    abi.require(pq_verify.verify(signer, msg, sig), b"bad_signature")

    if escrow_mode():
        abi.call_contract(_token(), "transfer_from", [caller, bytes(abi.contract_address()), amt])
    else:
        abi.call_contract(_token(), "burn_from", [caller, amt])

    req_id = _next_request_id()
    _uset(_k_req_exists(req_id), 1)
    _bset(_k_req_user(req_id), caller)
    _uset(_k_req_amount(req_id), amt)
    _bset(_k_req_bank(req_id), bank_ref)
    _uset(_k_req_status(req_id), _status_pending())
    _uset(_k_req_created(req_id), now_block)
    _uset(_k_req_updated(req_id), now_block)
    _uset(_k_nonce(caller, n), 1)

    events.emit(
        b"RedemptionRequested",
        {
            "requestId": req_id,
            "redeemer": caller,
            "amount": amt,
            "bankAccountRef": bank_ref,
            "nonce": n,
            "validBeforeBlock": int(valid_before_block),
            "escrowMode": escrow_mode(),
        },
    )
    return req_id


def get_redemption(request_id: bytes) -> dict:
    _ensure_init()
    req_id = bytes(request_id)
    if _uget(_k_req_exists(req_id)) != 1:
        return {"exists": False}

    status_code = _uget(_k_req_status(req_id))
    return {
        "exists": True,
        "requestId": req_id,
        "redeemer": _bget(_k_req_user(req_id)),
        "amount": _uget(_k_req_amount(req_id)),
        "bankAccountRef": _bget(_k_req_bank(req_id)),
        "status": status_name(status_code),
        "statusCode": status_code,
        "createdBlock": _uget(_k_req_created(req_id)),
        "updatedBlock": _uget(_k_req_updated(req_id)),
        "cancelReason": _bget(_k_req_cancel_reason(req_id)),
        "payoutReference": _bget(_k_req_payout_ref(req_id)),
        "escrowMode": escrow_mode(),
    }


def cancel_redemption(request_id: bytes, cancel_reason: bytes) -> None:
    _ensure_init()
    _ensure_operator()

    req_id = bytes(request_id)
    abi.require(_uget(_k_req_exists(req_id)) == 1, b"unknown_request")
    abi.require(_uget(_k_req_status(req_id)) == _status_pending(), b"not_pending")

    user = _bget(_k_req_user(req_id))
    amount = _uget(_k_req_amount(req_id))

    abi.require(escrow_mode(), b"cancel_requires_escrow")
    abi.call_contract(_token(), "transfer", [user, amount])

    _uset(_k_req_status(req_id), _status_cancelled())
    _uset(_k_req_updated(req_id), int(abi.block_height()))
    _bset(_k_req_cancel_reason(req_id), bytes(cancel_reason))

    events.emit(
        b"RedemptionCancelled",
        {
            "requestId": req_id,
            "redeemer": user,
            "amount": amount,
            "reason": bytes(cancel_reason),
            "cancelledBy": bytes(abi.caller()),
        },
    )


def resolve_redemption(request_id: bytes, payout_reference: bytes) -> None:
    _ensure_init()
    _ensure_operator()

    req_id = bytes(request_id)
    abi.require(_uget(_k_req_exists(req_id)) == 1, b"unknown_request")
    abi.require(_uget(_k_req_status(req_id)) == _status_pending(), b"not_pending")

    user = _bget(_k_req_user(req_id))
    amount = _uget(_k_req_amount(req_id))

    if escrow_mode():
        abi.call_contract(_token(), "burn", [amount])

    _uset(_k_req_status(req_id), _status_resolved())
    _uset(_k_req_updated(req_id), int(abi.block_height()))
    _bset(_k_req_payout_ref(req_id), bytes(payout_reference))

    events.emit(
        b"RedemptionResolved",
        {
            "requestId": req_id,
            "redeemer": user,
            "amount": amount,
            "payoutReference": bytes(payout_reference),
            "resolvedBy": bytes(abi.caller()),
            "escrowMode": escrow_mode(),
        },
    )
