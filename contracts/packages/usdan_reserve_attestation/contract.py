from __future__ import annotations

from stdlib import abi, events, hash, pq_verify, storage

K_INIT = b"usdan:reserve:init"
K_OWNER = b"usdan:reserve:owner"
K_LATEST_ATTESTATION_ID = b"usdan:reserve:latest_id"
K_LATEST_BLOCK = b"usdan:reserve:latest_block"


def _k_submitter(account: bytes) -> bytes:
    return b"usdan:reserve:submitter:" + bytes(account)


def _k_exists(attestation_id: bytes) -> bytes:
    return b"usdan:reserve:exists:" + bytes(attestation_id)


def _k_statement_hash(attestation_id: bytes) -> bytes:
    return b"usdan:reserve:statement_hash:" + bytes(attestation_id)


def _k_statement_uri(attestation_id: bytes) -> bytes:
    return b"usdan:reserve:statement_uri:" + bytes(attestation_id)


def _k_reserve_amount(attestation_id: bytes) -> bytes:
    return b"usdan:reserve:amount:" + bytes(attestation_id)


def _k_liability_amount(attestation_id: bytes) -> bytes:
    return b"usdan:reserve:liability:" + bytes(attestation_id)


def _k_as_of_block(attestation_id: bytes) -> bytes:
    return b"usdan:reserve:asof_block:" + bytes(attestation_id)


def _k_signer_hash(attestation_id: bytes) -> bytes:
    return b"usdan:reserve:signer_hash:" + bytes(attestation_id)


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


def _is_submitter(account: bytes) -> bool:
    if bytes(account) == _owner():
        return True
    return _uget(_k_submitter(bytes(account))) == 1


def _ensure_owner() -> None:
    abi.require(bytes(abi.caller()) == _owner(), b"not_owner")


def _ensure_submitter() -> None:
    abi.require(_is_submitter(bytes(abi.caller())), b"not_submitter")


def _attestation_message(
    attestation_id: bytes,
    statement_hash: bytes,
    statement_uri: bytes,
    reserve_amount: int,
    liability_amount: int,
    as_of_block: int,
) -> bytes:
    payload = (
        b"USDAN_RESERVE|"
        + str(int(abi.chain_id())).encode("utf-8")
        + b"|"
        + bytes(abi.contract_address())
        + b"|"
        + bytes(attestation_id)
        + b"|"
        + bytes(statement_hash)
        + b"|"
        + bytes(statement_uri)
        + b"|"
        + int(reserve_amount).to_bytes(32, "big")
        + b"|"
        + int(liability_amount).to_bytes(32, "big")
        + b"|"
        + int(as_of_block).to_bytes(8, "big")
    )
    return hash.sha3_256(payload)


def init(owner: bytes) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")
    _ensure_nonzero_addr(owner, b"bad_owner")

    _bset(K_OWNER, bytes(owner))
    _uset(_k_submitter(bytes(owner)), 1)
    _uset(K_INIT, 1)


def owner() -> bytes:
    _ensure_init()
    return _owner()


def is_submitter(account: bytes) -> bool:
    _ensure_init()
    return _is_submitter(bytes(account))


def latest_attestation_id() -> bytes:
    _ensure_init()
    return _bget(K_LATEST_ATTESTATION_ID)


def latest_attestation_block() -> int:
    _ensure_init()
    return _uget(K_LATEST_BLOCK)


def attestation_message(
    attestation_id: bytes,
    statement_hash: bytes,
    statement_uri: bytes,
    reserve_amount: int,
    liability_amount: int,
    as_of_block: int,
) -> bytes:
    _ensure_init()
    return _attestation_message(
        bytes(attestation_id),
        bytes(statement_hash),
        bytes(statement_uri),
        int(reserve_amount),
        int(liability_amount),
        int(as_of_block),
    )


def set_owner(new_owner: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    n = bytes(new_owner)
    _ensure_nonzero_addr(n, b"bad_owner")
    prev = _owner()
    _bset(K_OWNER, n)
    _uset(_k_submitter(n), 1)
    events.emit(b"OwnershipTransferred", {"previousOwner": prev, "newOwner": n})


def set_submitter(account: bytes, enabled: bool) -> None:
    _ensure_init()
    _ensure_owner()
    a = bytes(account)
    _ensure_nonzero_addr(a, b"bad_account")
    _uset(_k_submitter(a), 1 if bool(enabled) else 0)
    events.emit(b"SubmitterUpdated", {"account": a, "enabled": bool(enabled)})


def get_attestation(attestation_id: bytes) -> dict:
    _ensure_init()
    att_id = bytes(attestation_id)
    if _uget(_k_exists(att_id)) != 1:
        return {"exists": False}

    reserve = _uget(_k_reserve_amount(att_id))
    liabilities = _uget(_k_liability_amount(att_id))
    if liabilities == 0:
        coverage_bps = 0 if reserve == 0 else 1_000_000_000
    else:
        coverage_bps = (reserve * 10_000) // liabilities

    return {
        "exists": True,
        "attestationId": att_id,
        "statementHash": _bget(_k_statement_hash(att_id)),
        "statementUri": _bget(_k_statement_uri(att_id)),
        "reserveAmount": reserve,
        "liabilityAmount": liabilities,
        "coverageBps": coverage_bps,
        "asOfBlock": _uget(_k_as_of_block(att_id)),
        "signerHash": _bget(_k_signer_hash(att_id)),
    }


def submit_attestation(
    attestation_id: bytes,
    statement_hash: bytes,
    statement_uri: bytes,
    reserve_amount: int,
    liability_amount: int,
    as_of_block: int,
    signer_pubkey: bytes,
    signature: bytes,
) -> None:
    _ensure_init()
    _ensure_submitter()

    att_id = bytes(attestation_id)
    st_hash = bytes(statement_hash)
    st_uri = bytes(statement_uri)
    signer = bytes(signer_pubkey)
    sig = bytes(signature)

    abi.require(len(att_id) > 0, b"bad_attestation_id")
    abi.require(len(st_hash) == 32, b"bad_statement_hash")
    abi.require(len(st_uri) > 0, b"bad_statement_uri")
    abi.require(int(reserve_amount) >= 0, b"bad_reserve")
    abi.require(int(liability_amount) >= 0, b"bad_liability")
    abi.require(_uget(_k_exists(att_id)) == 0, b"attestation_exists")

    msg = _attestation_message(att_id, st_hash, st_uri, int(reserve_amount), int(liability_amount), int(as_of_block))
    abi.require(pq_verify.verify(signer, msg, sig), b"bad_signature")

    _uset(_k_exists(att_id), 1)
    _bset(_k_statement_hash(att_id), st_hash)
    _bset(_k_statement_uri(att_id), st_uri)
    _uset(_k_reserve_amount(att_id), int(reserve_amount))
    _uset(_k_liability_amount(att_id), int(liability_amount))
    _uset(_k_as_of_block(att_id), int(as_of_block))
    _bset(_k_signer_hash(att_id), hash.sha3_256(signer))

    _bset(K_LATEST_ATTESTATION_ID, att_id)
    _uset(K_LATEST_BLOCK, int(abi.block_height()))

    if int(liability_amount) == 0:
        coverage_bps = 0 if int(reserve_amount) == 0 else 1_000_000_000
    else:
        coverage_bps = (int(reserve_amount) * 10_000) // int(liability_amount)

    events.emit(
        b"ReserveAttested",
        {
            "attestationId": att_id,
            "statementHash": st_hash,
            "statementUri": st_uri,
            "reserveAmount": int(reserve_amount),
            "liabilityAmount": int(liability_amount),
            "coverageBps": coverage_bps,
            "asOfBlock": int(as_of_block),
            "submittedBy": bytes(abi.caller()),
            "signerHash": hash.sha3_256(signer),
        },
    )
