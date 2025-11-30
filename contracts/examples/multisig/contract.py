# Multisig (N-of-M, PQ-aware permits) — deterministic Python contract
#
# This contract implements a threshold multisig with two execution paths:
#  - On-chain approvals: propose → approve (owners) → execute (once approvals ≥ threshold)
#  - One-shot permits: off-chain PQ-signed permits verified on-chain in a single call
#
# Notes on determinism:
#  - No wall-clock time; expiry is checked against block height.
#  - Hashing is sha3-256 with fixed-width integer encodings.
#  - Storage layout is explicit and prefix-scoped.
#  - External call execution is represented as an emitted event (ret bytes),
#    as cross-contract calls require host integration. The ABI is forward-compatible.

from stdlib import abi, events, hash, storage  # deterministic stdlib surface

# ────────────────────────────────────────────────────────────────────────────────
# Types (as bytes / integers)
# - address: 32-byte canonical address (bech32 is a UI concern, not in-contract)
# - u8/u16/u64/u128: big-endian fixed-width encodings when serialized
# - bytes32: 32-byte value
# ────────────────────────────────────────────────────────────────────────────────

# Domain separator (bytes32) for permits (stable & versioned)
_PERMIT_DOMAIN = hash.sha3_256(b"animica.multisig.permit.v1")

# Storage key prefixes (never change once deployed)
K_THRESHOLD = b"\x01thr"  # u8
K_NONCE = b"\x01nonce"  # u128 (next)
K_OWNER_LIST = b"\x02owners.list"  # packed addresses (32*N)
P_OWNER_REC = b"\x02owner."  # + address -> OwnerRecord
P_ACTION = b"\x03action."  # + nonce(16) -> Encoded Action
P_APPROVED = b"\x03approved."  # + nonce(16) + address -> b"\x01"
P_APPCOUNT = b"\x03appcount."  # + nonce(16) -> u32 (approval count)
P_PROPOSED = b"\x03proposed."  # + nonce(16) -> b"\x01" (sentinel)


# OwnerRecord serialization:
#   alg_id (u16) || pubkey_hash (bytes32) || active (u8: 0/1)
def _enc_owner_record(alg_id: int, pubkey_hash: bytes, active: bool) -> bytes:
    abi.require(0 <= alg_id < 65536, b"AlgIdOutOfRange")
    abi.require(len(pubkey_hash) == 32, b"BadPubkeyHash")
    return _u16(alg_id) + pubkey_hash + (b"\x01" if active else b"\x00")


def _dec_owner_record(b: bytes) -> tuple[int, bytes, bool]:
    abi.require(len(b) == 2 + 32 + 1, b"OwnerRecordLen")
    alg = int.from_bytes(b[0:2], "big")
    pk = b[2:34]
    act = b[34] == 1
    return (alg, pk, act)


# Action serialization for hashing:
#   tag 'A' || to(32) || value(u128) || gas_limit(u64) || data_len(u32) || data
def _enc_action(to: bytes, value: int, data: bytes, gas_limit: int) -> bytes:
    abi.require(len(to) == 32, b"AddrLen")
    abi.require(value >= 0, b"ValueNegative")
    abi.require(0 <= gas_limit < (1 << 64), b"GasLimitRange")
    dl = len(data)
    abi.require(0 <= dl < (1 << 32), b"DataTooLarge")
    return b"A" + to + _u128(value) + _u64(gas_limit) + _u32(dl) + data


def _action_hash(to: bytes, value: int, data: bytes, gas_limit: int) -> bytes:
    return hash.sha3_256(_enc_action(to, value, data, gas_limit))


# Fixed-width integer encoders
def _u8(x: int) -> bytes:
    abi.require(0 <= x < (1 << 8), b"U8")
    return x.to_bytes(1, "big")


def _u16(x: int) -> bytes:
    abi.require(0 <= x < (1 << 16), b"U16")
    return x.to_bytes(2, "big")


def _u32(x: int) -> bytes:
    abi.require(0 <= x < (1 << 32), b"U32")
    return x.to_bytes(4, "big")


def _u64(x: int) -> bytes:
    abi.require(0 <= x < (1 << 64), b"U64")
    return x.to_bytes(8, "big")


def _u128(x: int) -> bytes:
    abi.require(0 <= x < (1 << 128), b"U128")
    return x.to_bytes(16, "big")


def _bytes32(x: bytes) -> bytes:
    abi.require(len(x) == 32, b"Bytes32")
    return x


def _addr(x: bytes) -> bytes:
    abi.require(len(x) == 32, b"Address")
    return x


def _nonce_key(nonce: int) -> bytes:
    return _u128(nonce)


# Internal: read/write helpers
def _load_u8(key: bytes, default: int = 0) -> int:
    v = storage.get(key)
    return v[0] if v else default


def _store_u8(key: bytes, val: int) -> None:
    storage.set(key, _u8(val))


def _load_u32(key: bytes, default: int = 0) -> int:
    v = storage.get(key)
    return int.from_bytes(v, "big") if v else default


def _store_u32(key: bytes, val: int) -> None:
    storage.set(key, _u32(val))


def _load_u128(key: bytes, default: int = 0) -> int:
    v = storage.get(key)
    return int.from_bytes(v, "big") if v else default


def _store_u128(key: bytes, val: int) -> None:
    storage.set(key, _u128(val))


# Owner list packing: 32*N contiguous bytes
def _owners_list_get() -> list[bytes]:
    blob = storage.get(K_OWNER_LIST)
    if not blob:
        return []
    abi.require(len(blob) % 32 == 0, b"OwnerListCorrupt")
    out: list[bytes] = []
    i = 0
    L = len(blob)
    while i < L:
        out.append(blob[i : i + 32])
        i += 32
    return out


def _owners_list_set(lst: list[bytes]) -> None:
    buf = b"".join(lst)
    storage.set(K_OWNER_LIST, buf)


def _owner_rec_key(owner: bytes) -> bytes:
    return P_OWNER_REC + owner


def _action_key(nonce: int) -> bytes:
    return P_ACTION + _nonce_key(nonce)


def _approved_key(nonce: int, owner: bytes) -> bytes:
    return P_APPROVED + _nonce_key(nonce) + owner


def _appcount_key(nonce: int) -> bytes:
    return P_APPCOUNT + _nonce_key(nonce)


def _proposed_key(nonce: int) -> bytes:
    return P_PROPOSED + _nonce_key(nonce)


# ────────────────────────────────────────────────────────────────────────────────
# ABI (docstring signatures for tooling):
#
# @view
# def get_config() -> {"owners": "address[]", "threshold": "u8"}
#
# @view
# def get_nonce() -> {"nonce": "u128"}
#
# @view
# def is_owner(addr: "address") -> {"active": "bool"}
#
# @view
# def permit_domain() -> {"domain": "bytes32"}
#
# def propose(to: "address", value: "u128", data: "bytes", gas_limit: "u64") -> {"nonce":"u128","action_hash":"bytes32"}
#
# def approve(nonce: "u128") -> {}
# def revoke(nonce: "u128") -> {}
# def execute(nonce: "u128") -> {"success":"bool","ret":"bytes"}
#
# def execute_with_permits(nonce:"u128", to:"address", value:"u128", data:"bytes", gas_limit:"u64",
#                          expiry_height:"u64",
#                          permits: [{"signer_addr":"address","alg_id":"u16","sig":"bytes","pubkey_hash":"bytes32"}]
#                        ) -> {"success":"bool","ret":"bytes"}
#
# # Governance (must be executed via multisig self-call)
# def set_threshold(new_threshold:"u8") -> {}
# def add_owner(addr:"address", alg_id:"u16", pubkey_hash:"bytes32") -> {}
# def remove_owner(addr:"address") -> {}
# def replace_owner(old:"address", new:"address", new_alg_id:"u16", new_pubkey_hash:"bytes32") -> {}
# ────────────────────────────────────────────────────────────────────────────────

# Initialization guard: if first call wants to seed owners/threshold, it can be done
# by executing multisig actions to itself after deploy; alternatively, deploy tools
# may pre-populate storage before publish. Here we lazily default threshold=1, nonce=0.


def _ensure_init_defaults() -> None:
    if storage.get(K_THRESHOLD) is None:
        _store_u8(K_THRESHOLD, 1)
    if storage.get(K_NONCE) is None:
        _store_u128(K_NONCE, 0)
    if storage.get(K_OWNER_LIST) is None:
        _owners_list_set([])


# ────────────────────────────────────────────────────────────────────────────────
# Views


def get_config() -> tuple[list[bytes], int]:
    """
    Return (owners, threshold)
    """
    _ensure_init_defaults()
    return (_owners_list_get(), _load_u8(K_THRESHOLD, 1))


def get_nonce() -> int:
    _ensure_init_defaults()
    return _load_u128(K_NONCE, 0)


def is_owner(addr: bytes) -> bool:
    _ensure_init_defaults()
    rec = storage.get(_owner_rec_key(_addr(addr)))
    if not rec:
        return False
    _, _, active = _dec_owner_record(rec)
    return active


def permit_domain() -> bytes:
    return _PERMIT_DOMAIN


# ────────────────────────────────────────────────────────────────────────────────
# On-chain approvals flow


def propose(to: bytes, value: int, data: bytes, gas_limit: int) -> tuple[int, bytes]:
    """
    Propose an action; returns (nonce, action_hash)
    """
    _ensure_init_defaults()
    caller = abi.msg_sender()
    abi.require(is_owner(caller), b"OnlyOwner")
    nonce = get_nonce()
    ah = _action_hash(_addr(to), int(value), data, int(gas_limit))
    abi.require(storage.get(_action_key(nonce)) is None, b"NonceTaken")
    storage.set(_action_key(nonce), _enc_action(to, int(value), data, int(gas_limit)))
    storage.set(_proposed_key(nonce), b"\x01")
    _store_u32(_appcount_key(nonce), 0)
    # Do not auto-approve; keep separation of duties
    _store_u128(K_NONCE, nonce + 1)  # reserve the nonce immediately
    events.emit(
        b"Proposed",
        {
            b"nonce": _u128(nonce),
            b"action_hash": _bytes32(ah),
            b"proposer": _addr(caller),
        },
    )
    return (nonce, ah)


def approve(nonce: int) -> None:
    _ensure_init_defaults()
    caller = abi.msg_sender()
    abi.require(is_owner(caller), b"OnlyOwner")
    abi.require(storage.get(_proposed_key(nonce)) is not None, b"NotProposed")
    k = _approved_key(nonce, caller)
    if storage.get(k) is not None:
        abi.revert(b"AlreadyApproved")
    storage.set(k, b"\x01")
    c = _load_u32(_appcount_key(nonce), 0) + 1
    _store_u32(_appcount_key(nonce), c)
    events.emit(b"Approved", {b"nonce": _u128(nonce), b"owner": _addr(caller)})


def revoke(nonce: int) -> None:
    _ensure_init_defaults()
    caller = abi.msg_sender()
    abi.require(is_owner(caller), b"OnlyOwner")
    abi.require(storage.get(_proposed_key(nonce)) is not None, b"NotProposed")
    k = _approved_key(nonce, caller)
    if storage.get(k) is None:
        abi.revert(b"NotApproved")
    storage.delete(k)
    c = _load_u32(_appcount_key(nonce), 0) - 1
    _store_u32(_appcount_key(nonce), c)
    events.emit(b"Revoked", {b"nonce": _u128(nonce), b"owner": _addr(caller)})


def execute(nonce: int) -> tuple[bool, bytes]:
    """
    Execute an approved action when approvals ≥ threshold.
    Emits Executed event with (success, ret).
    """
    _ensure_init_defaults()
    # Ensure proposal exists
    enc = storage.get(_action_key(nonce))
    abi.require(
        enc is not None and storage.get(_proposed_key(nonce)) == b"\x01", b"NotProposed"
    )
    # Check approvals
    threshold = _load_u8(K_THRESHOLD, 1)
    abi.require(threshold > 0, b"BadThreshold")
    approved = _load_u32(_appcount_key(nonce), 0)
    abi.require(approved >= threshold, b"NotEnoughApprovals")
    # Decode action — deterministic representation (for future call adapter)
    # Layout: 'A' | to(32) | value(16) | gas(8) | dl(4) | data
    abi.require(len(enc) >= 1 + 32 + 16 + 8 + 4, b"ActionCorrupt")
    to = enc[1:33]
    value = int.from_bytes(enc[33:49], "big")
    gas_limit = int.from_bytes(enc[49:57], "big")
    dl = int.from_bytes(enc[57:61], "big")
    data = enc[61 : 61 + dl]
    # Placeholder "call": we emit the event and return empty bytes.
    # (A real call adapter would be wired by the host to run target code deterministically.)
    success = True
    ret = b""
    # Clear proposal records to prevent re-execution
    storage.delete(_action_key(nonce))
    storage.delete(_proposed_key(nonce))
    storage.delete(_appcount_key(nonce))
    # (We purposely do not iterate to delete each individual approval slot
    #  as storage API has no iteration; such entries are harmless after nonce consumed.)
    events.emit(
        b"Executed",
        {
            b"nonce": _u128(nonce),
            b"action_hash": _bytes32(_action_hash(to, value, data, gas_limit)),
            b"success": b"\x01" if success else b"\x00",
            b"ret": ret,
        },
    )
    return (success, ret)


# ────────────────────────────────────────────────────────────────────────────────
# One-shot permits flow (PQ-aware skeleton)
#
# In a fully wired environment, this function would:
#   * Rebuild SignBytes
#   * Verify each PQ signature against registered owner key material
#   * Enforce unique signers and threshold
#   * Execute the action
#
# Here we implement deterministic structure checks and owner/key binding. The actual
# PQ signature verification is represented as a structured check (sig length > 0).
# Replace `_verify_permit_sig` with a host-provided verifier when available.


def _signbytes(
    chain_id: int,
    contract_addr: bytes,
    nonce: int,
    action_hash: bytes,
    expiry_height: int,
    alg_id: int,
) -> bytes:
    abi.require(len(contract_addr) == 32, b"ContractAddr")
    return (
        _PERMIT_DOMAIN
        + _u64(chain_id)
        + contract_addr
        + _u128(nonce)
        + _bytes32(action_hash)
        + _u64(expiry_height)
        + _u16(alg_id)
    )


def _verify_permit_sig(
    sign_bytes: bytes, alg_id: int, pubkey_hash: bytes, sig: bytes
) -> bool:
    # Deterministic placeholder:
    # Accept if sig == sha3_256(pubkey_hash || sign_bytes) to permit local tests.
    # Replace with real PQ verify when host capability is exposed.
    expected = hash.sha3_256(pubkey_hash + sign_bytes)
    return sig == expected


def execute_with_permits(
    nonce: int,
    to: bytes,
    value: int,
    data: bytes,
    gas_limit: int,
    expiry_height: int,
    permits: list[dict],
) -> tuple[bool, bytes]:
    _ensure_init_defaults()
    # Check that this matches the next nonce already reserved by propose? For permits
    # we allow direct execution on an arbitrary nonce, but it MUST be equal to current
    # expected nonce to avoid replay across nonces.
    expected_nonce = get_nonce()
    abi.require(nonce == expected_nonce, b"NonceMismatch")

    # Build deterministic action hash
    ah = _action_hash(_addr(to), int(value), data, int(gas_limit))

    # Height check
    current_height = abi.block_height()
    abi.require(current_height <= int(expiry_height), b"PermitExpired")

    # Threshold
    owners = _owners_list_get()
    abi.require(len(owners) > 0, b"NoOwners")
    threshold = _load_u8(K_THRESHOLD, 1)
    abi.require(1 <= threshold <= len(owners), b"BadThreshold")

    # Unique signers, all must be active owners with matching key material
    seen: dict[bytes, bool] = {}
    ok_count = 0

    chain_id = abi.chain_id()
    contract_addr = abi.contract_address()

    for p in permits:
        signer_addr = _addr(p[b"signer_addr"])
        alg_id = (
            int.from_bytes(_u16(int.from_bytes(p[b"alg_id"], "big")), "big")
            if isinstance(p[b"alg_id"], (bytes, bytearray))
            else int(p[b"alg_id"])
        )
        pubkey_hash = _bytes32(p[b"pubkey_hash"])
        sig = p[b"sig"]
        # Check owner & key pin
        rec_b = storage.get(_owner_rec_key(signer_addr))
        abi.require(rec_b is not None, b"UnknownOwner")
        rec_alg, rec_pkh, rec_active = _dec_owner_record(rec_b)
        abi.require(rec_active, b"OwnerInactive")
        abi.require(rec_alg == alg_id, b"AlgIdMismatch")
        abi.require(rec_pkh == pubkey_hash, b"PubkeyHashMismatch")
        # Unique
        if signer_addr in seen:
            abi.revert(b"DuplicateSigner")
        seen[signer_addr] = True
        # Verify signature (placeholder)
        sb = _signbytes(
            int(chain_id),
            _addr(contract_addr),
            int(nonce),
            _bytes32(ah),
            int(expiry_height),
            int(alg_id),
        )
        abi.require(len(sig) > 0, b"EmptySig")
        abi.require(_verify_permit_sig(sb, alg_id, pubkey_hash, sig), b"SigInvalid")
        ok_count += 1

    abi.require(ok_count >= threshold, b"NotEnoughPermits")

    # Bump nonce (consumed), then emit and "execute"
    _store_u128(K_NONCE, expected_nonce + 1)

    success = True
    ret = b""
    events.emit(
        b"Executed",
        {
            b"nonce": _u128(nonce),
            b"action_hash": _bytes32(ah),
            b"success": b"\x01" if success else b"\x00",
            b"ret": ret,
        },
    )
    return (success, ret)


# ────────────────────────────────────────────────────────────────────────────────
# Governance — MUST be executed via multisig (self-call)


def _require_self_call() -> None:
    abi.require(abi.msg_sender() == abi.contract_address(), b"OnlySelfCall")


def set_threshold(new_threshold: int) -> None:
    _ensure_init_defaults()
    _require_self_call()
    owners = _owners_list_get()
    abi.require(
        1 <= new_threshold <= len(owners) or (len(owners) == 0 and new_threshold == 1),
        b"ThresholdInvalid",
    )
    _store_u8(K_THRESHOLD, int(new_threshold))
    events.emit(
        b"OwnersChanged",
        {b"owners": b"".join(owners), b"threshold": _u8(int(new_threshold))},
    )


def add_owner(addr: bytes, alg_id: int, pubkey_hash: bytes) -> None:
    _ensure_init_defaults()
    _require_self_call()
    owner = _addr(addr)
    lst = _owners_list_get()
    # Ensure not present
    for a in lst:
        if a == owner:
            abi.revert(b"OwnerExists")
    lst.append(owner)
    _owners_list_set(lst)
    storage.set(
        _owner_rec_key(owner),
        _enc_owner_record(int(alg_id), _bytes32(pubkey_hash), True),
    )
    # Adjust threshold if it was 0 due to empty set (bootstrapping)
    thr = _load_u8(K_THRESHOLD, 1)
    if thr == 0:
        _store_u8(K_THRESHOLD, 1)
    events.emit(
        b"OwnersChanged",
        {b"owners": b"".join(lst), b"threshold": _u8(_load_u8(K_THRESHOLD, 1))},
    )
    events.emit(
        b"OwnerKeyUpdated",
        {
            b"owner": owner,
            b"alg_id": _u16(int(alg_id)),
            b"pubkey_hash": _bytes32(pubkey_hash),
        },
    )


def remove_owner(addr: bytes) -> None:
    _ensure_init_defaults()
    _require_self_call()
    owner = _addr(addr)
    lst = _owners_list_get()
    new_lst: list[bytes] = []
    found = False
    for a in lst:
        if a == owner:
            found = True
        else:
            new_lst.append(a)
    abi.require(found, b"OwnerNotFound")
    _owners_list_set(new_lst)
    # Mark record inactive (retain key history for audit)
    rec = storage.get(_owner_rec_key(owner))
    if rec:
        alg, pkh, _ = _dec_owner_record(rec)
        storage.set(_owner_rec_key(owner), _enc_owner_record(alg, pkh, False))
    # Clamp threshold if needed
    thr = _load_u8(K_THRESHOLD, 1)
    if thr > len(new_lst):
        _store_u8(K_THRESHOLD, len(new_lst) if len(new_lst) > 0 else 0)
    events.emit(
        b"OwnersChanged",
        {b"owners": b"".join(new_lst), b"threshold": _u8(_load_u8(K_THRESHOLD, 0))},
    )


def replace_owner(
    old: bytes, new: bytes, new_alg_id: int, new_pubkey_hash: bytes
) -> None:
    _ensure_init_defaults()
    _require_self_call()
    old_a = _addr(old)
    new_a = _addr(new)
    # Remove old
    lst = _owners_list_get()
    replaced = False
    for i in range(len(lst)):
        if lst[i] == old_a:
            lst[i] = new_a
            replaced = True
            break
    abi.require(replaced, b"OwnerNotFound")
    _owners_list_set(lst)
    # Deactivate old key record (retain history)
    rec = storage.get(_owner_rec_key(old_a))
    if rec:
        alg, pkh, _ = _dec_owner_record(rec)
        storage.set(_owner_rec_key(old_a), _enc_owner_record(alg, pkh, False))
    # Set new record active
    storage.set(
        _owner_rec_key(new_a),
        _enc_owner_record(int(new_alg_id), _bytes32(new_pubkey_hash), True),
    )
    events.emit(
        b"OwnersChanged",
        {b"owners": b"".join(lst), b"threshold": _u8(_load_u8(K_THRESHOLD, 1))},
    )
    events.emit(
        b"OwnerKeyUpdated",
        {
            b"owner": new_a,
            b"alg_id": _u16(int(new_alg_id)),
            b"pubkey_hash": _bytes32(new_pubkey_hash),
        },
    )


# ────────────────────────────────────────────────────────────────────────────────
# End of file
