from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"aicf:modelcall:init"
K_OWNER = b"aicf:modelcall:owner"
K_PAUSED = b"aicf:modelcall:paused"

# state codes
# 1=requested, 2=claimed, 3=commitment_submitted, 4=result_submitted,
# 5=accepted, 6=challenged, 7=finalized, 8=refunded


def _k_state(call_id: bytes) -> bytes:
    return b"aicf:modelcall:state:" + bytes(call_id)


def _k_requester(call_id: bytes) -> bytes:
    return b"aicf:modelcall:requester:" + bytes(call_id)


def _k_payer(call_id: bytes) -> bytes:
    return b"aicf:modelcall:payer:" + bytes(call_id)


def _k_model_id(call_id: bytes) -> bytes:
    return b"aicf:modelcall:model:" + bytes(call_id)


def _k_job_type(call_id: bytes) -> bytes:
    return b"aicf:modelcall:jobtype:" + bytes(call_id)


def _k_input_ref_hash(call_id: bytes) -> bytes:
    return b"aicf:modelcall:inputhash:" + bytes(call_id)


def _k_output_schema(call_id: bytes) -> bytes:
    return b"aicf:modelcall:outschema:" + bytes(call_id)


def _k_max_budget(call_id: bytes) -> bytes:
    return b"aicf:modelcall:maxbudget:" + bytes(call_id)


def _k_timeout_height(call_id: bytes) -> bytes:
    return b"aicf:modelcall:timeout:" + bytes(call_id)


def _k_replication(call_id: bytes) -> bytes:
    return b"aicf:modelcall:replication:" + bytes(call_id)


def _k_quorum_mode(call_id: bytes) -> bytes:
    return b"aicf:modelcall:quorummode:" + bytes(call_id)


def _k_quorum_required(call_id: bytes) -> bytes:
    return b"aicf:modelcall:quorumrequired:" + bytes(call_id)


def _k_challenge_window(call_id: bytes) -> bytes:
    return b"aicf:modelcall:challenge:" + bytes(call_id)


def _k_provider_policy(call_id: bytes) -> bytes:
    return b"aicf:modelcall:providerpolicy:" + bytes(call_id)


def _k_privacy(call_id: bytes) -> bytes:
    return b"aicf:modelcall:privacy:" + bytes(call_id)


def _k_callback_mode(call_id: bytes) -> bytes:
    return b"aicf:modelcall:callback:" + bytes(call_id)


def _k_claimed_provider(call_id: bytes) -> bytes:
    return b"aicf:modelcall:claimedprovider:" + bytes(call_id)


def _k_commit_height(call_id: bytes) -> bytes:
    return b"aicf:modelcall:commitheight:" + bytes(call_id)


def _k_result_hash(call_id: bytes) -> bytes:
    return b"aicf:modelcall:resulthash:" + bytes(call_id)


def _k_result_ref(call_id: bytes) -> bytes:
    return b"aicf:modelcall:resultref:" + bytes(call_id)


def _k_metadata_ref(call_id: bytes) -> bytes:
    return b"aicf:modelcall:metadata:" + bytes(call_id)


def _k_provider_sig(call_id: bytes) -> bytes:
    return b"aicf:modelcall:providersig:" + bytes(call_id)


def _k_accepted_hash(call_id: bytes) -> bytes:
    return b"aicf:modelcall:acceptedhash:" + bytes(call_id)


def _k_challenge_reason(call_id: bytes) -> bytes:
    return b"aicf:modelcall:challengereason:" + bytes(call_id)


def _k_challenge_evidence(call_id: bytes) -> bytes:
    return b"aicf:modelcall:challengeevidence:" + bytes(call_id)


def _k_nonce(tag: bytes, nonce: bytes) -> bytes:
    return b"aicf:modelcall:nonce:" + bytes(tag) + b":" + bytes(nonce)


def _uget(key: bytes) -> int:
    raw = storage.get(key)
    if raw in (None, b""):
        return 0
    return int.from_bytes(raw, "big")


def _uset(key: bytes, value: int) -> None:
    v = int(value)
    abi.require(v >= 0, b"negative")
    if v == 0:
        storage.set(key, b"")
        return
    storage.set(key, v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big"))


def _bget(key: bytes) -> bytes:
    raw = storage.get(key)
    if raw is None:
        return b""
    return raw


def _bset(key: bytes, value: bytes) -> None:
    storage.set(key, bytes(value))


def _ensure_init() -> None:
    abi.require(_uget(K_INIT) == 1, b"not_initialized")


def _ensure_owner(actor: bytes) -> None:
    abi.require(bytes(actor) == _bget(K_OWNER), b"not_owner")


def _ensure_not_paused() -> None:
    abi.require(_uget(K_PAUSED) == 0, b"paused")


def init(owner: bytes) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")
    abi.require(len(bytes(owner)) > 0, b"bad_owner")
    _bset(K_OWNER, bytes(owner))
    _uset(K_PAUSED, 0)
    _uset(K_INIT, 1)


def pause(actor: bytes, paused_state: bool) -> None:
    _ensure_init()
    _ensure_owner(actor)
    _uset(K_PAUSED, 1 if paused_state else 0)


def request_model_call(
    actor: bytes,
    nonce: bytes,
    call_id: bytes,
    requester_contract: bytes,
    payer: bytes,
    model_id: bytes,
    job_type: bytes,
    prompt_input_ref_hash: bytes,
    output_schema_requirement: bytes,
    max_anm_budget: int,
    timeout_height: int,
    replication: int,
    quorum_mode: int,
    quorum_required: int,
    challenge_window: int,
    provider_policy: bytes,
    privacy_flag: int,
    callback_mode: int,
) -> None:
    _ensure_init()
    _ensure_not_paused()
    abi.require(_uget(_k_nonce(b"request", nonce)) == 0, b"replay_request")
    abi.require(_uget(_k_state(call_id)) == 0, b"call_exists")

    budget = int(max_anm_budget)
    timeout = int(timeout_height)
    repl = int(replication)
    mode = int(quorum_mode)
    quorum = int(quorum_required)
    window = int(challenge_window)
    privacy = int(privacy_flag)
    callback = int(callback_mode)

    abi.require(budget > 0, b"bad_budget")
    abi.require(timeout > 0, b"bad_timeout")
    abi.require(repl >= 1, b"bad_replication")
    abi.require(mode in (1, 2, 3, 4), b"bad_mode")
    abi.require(quorum >= 1 and quorum <= repl, b"bad_quorum")
    abi.require(window > 0, b"bad_window")
    abi.require(privacy in (0, 1), b"bad_privacy")
    abi.require(callback in (0, 1, 2), b"bad_callback")

    _bset(_k_requester(call_id), bytes(requester_contract))
    _bset(_k_payer(call_id), bytes(payer))
    _bset(_k_model_id(call_id), bytes(model_id))
    _bset(_k_job_type(call_id), bytes(job_type))
    _bset(_k_input_ref_hash(call_id), bytes(prompt_input_ref_hash))
    _bset(_k_output_schema(call_id), bytes(output_schema_requirement))
    _uset(_k_max_budget(call_id), budget)
    _uset(_k_timeout_height(call_id), timeout)
    _uset(_k_replication(call_id), repl)
    _uset(_k_quorum_mode(call_id), mode)
    _uset(_k_quorum_required(call_id), quorum)
    _uset(_k_challenge_window(call_id), window)
    _bset(_k_provider_policy(call_id), bytes(provider_policy))
    _uset(_k_privacy(call_id), privacy)
    _uset(_k_callback_mode(call_id), callback)
    _uset(_k_state(call_id), 1)
    _uset(_k_nonce(b"request", nonce), 1)

    events.emit(
        b"ModelCallRequested",
        {
            "actor": bytes(actor),
            "nonce": bytes(nonce),
            "call_id": bytes(call_id),
            "requester_contract": bytes(requester_contract),
            "payer": bytes(payer),
            "model_id": bytes(model_id),
            "budget": budget,
            "mode": mode,
        },
    )


def claim_call(actor: bytes, call_id: bytes, provider_id: bytes, now_height: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    abi.require(_uget(_k_state(call_id)) == 1, b"not_request_state")
    abi.require(_bget(_k_claimed_provider(call_id)) == b"", b"already_claimed")
    abi.require(int(now_height) <= _uget(_k_timeout_height(call_id)), b"expired")

    _bset(_k_claimed_provider(call_id), bytes(provider_id))
    _uset(_k_state(call_id), 2)

    events.emit(
        b"ModelCallClaimed",
        {
            "actor": bytes(actor),
            "call_id": bytes(call_id),
            "provider_id": bytes(provider_id),
        },
    )


def submit_result_commitment(
    actor: bytes,
    nonce: bytes,
    call_id: bytes,
    provider_id: bytes,
    result_hash: bytes,
    provider_signature: bytes,
    metadata_reference: bytes,
    now_height: int,
) -> None:
    _ensure_init()
    _ensure_not_paused()
    abi.require(_uget(_k_nonce(b"commit", nonce)) == 0, b"replay_commit")
    state = _uget(_k_state(call_id))
    abi.require(state in (2, 3), b"bad_state")
    abi.require(bytes(provider_id) == _bget(_k_claimed_provider(call_id)), b"not_claimed_provider")
    abi.require(int(now_height) <= _uget(_k_timeout_height(call_id)), b"expired")

    _bset(_k_result_hash(call_id), bytes(result_hash))
    _bset(_k_provider_sig(call_id), bytes(provider_signature))
    _bset(_k_metadata_ref(call_id), bytes(metadata_reference))
    _uset(_k_commit_height(call_id), int(now_height))
    _uset(_k_state(call_id), 3)
    _uset(_k_nonce(b"commit", nonce), 1)

    events.emit(
        b"ResultCommitmentSubmitted",
        {
            "actor": bytes(actor),
            "nonce": bytes(nonce),
            "call_id": bytes(call_id),
            "provider_id": bytes(provider_id),
            "result_hash": bytes(result_hash),
        },
    )


def submit_result_reference(actor: bytes, nonce: bytes, call_id: bytes, result_reference: bytes) -> None:
    _ensure_init()
    _ensure_not_paused()
    abi.require(_uget(_k_nonce(b"result", nonce)) == 0, b"replay_result")
    state = _uget(_k_state(call_id))
    abi.require(state in (3, 4, 5), b"bad_state")

    _bset(_k_result_ref(call_id), bytes(result_reference))
    if state < 4:
        _uset(_k_state(call_id), 4)
    _uset(_k_nonce(b"result", nonce), 1)

    events.emit(
        b"ResultReferenceSubmitted",
        {
            "actor": bytes(actor),
            "nonce": bytes(nonce),
            "call_id": bytes(call_id),
            "result_reference": bytes(result_reference),
        },
    )


def accept_result(actor: bytes, call_id: bytes, accepted_hash: bytes) -> None:
    _ensure_init()
    _ensure_not_paused()
    state = _uget(_k_state(call_id))
    abi.require(state in (3, 4, 5), b"bad_state")
    abi.require(_bget(_k_result_hash(call_id)) == bytes(accepted_hash), b"hash_mismatch")

    _bset(_k_accepted_hash(call_id), bytes(accepted_hash))
    _uset(_k_state(call_id), 5)

    events.emit(
        b"ResultAccepted",
        {
            "actor": bytes(actor),
            "call_id": bytes(call_id),
            "accepted_hash": bytes(accepted_hash),
        },
    )


def challenge_result(
    actor: bytes,
    call_id: bytes,
    reason_code: bytes,
    evidence_reference: bytes,
    now_height: int,
) -> None:
    _ensure_init()
    _ensure_not_paused()

    state = _uget(_k_state(call_id))
    abi.require(state in (3, 4, 5), b"bad_state")
    commit_height = _uget(_k_commit_height(call_id))
    window = _uget(_k_challenge_window(call_id))
    abi.require(commit_height > 0, b"no_commit")
    abi.require(int(now_height) <= commit_height + window, b"window_closed")

    _bset(_k_challenge_reason(call_id), bytes(reason_code))
    _bset(_k_challenge_evidence(call_id), bytes(evidence_reference))
    _uset(_k_state(call_id), 6)

    events.emit(
        b"ResultChallenged",
        {
            "actor": bytes(actor),
            "call_id": bytes(call_id),
            "reason_code": bytes(reason_code),
            "evidence_ref": bytes(evidence_reference),
        },
    )


def finalize_result(actor: bytes, call_id: bytes, now_height: int) -> None:
    _ensure_init()
    _ensure_not_paused()

    state = _uget(_k_state(call_id))
    abi.require(state in (4, 5), b"bad_state")
    abi.require(_bget(_k_result_ref(call_id)) != b"", b"missing_result_ref")

    mode = _uget(_k_quorum_mode(call_id))
    callback = _uget(_k_callback_mode(call_id))
    commit_height = _uget(_k_commit_height(call_id))
    window = _uget(_k_challenge_window(call_id))

    # CALLBACK_ACCEPT and VERIFIER_REVIEW require accept_result prior to finalize
    if mode in (3, 4) or callback in (1, 2):
        abi.require(state == 5, b"must_accept_first")

    # SINGLE_PROVIDER and QUORUM_MATCH require window to elapse if not explicit accept path
    if state != 5:
        abi.require(int(now_height) >= commit_height + window, b"challenge_window_open")

    _uset(_k_state(call_id), 7)

    events.emit(
        b"ResultFinalized",
        {
            "actor": bytes(actor),
            "call_id": bytes(call_id),
            "mode": mode,
            "state_before": state,
        },
    )


def refund_if_expired(actor: bytes, call_id: bytes, now_height: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    state = _uget(_k_state(call_id))
    abi.require(state not in (7, 8), b"already_closed")
    abi.require(int(now_height) > _uget(_k_timeout_height(call_id)), b"not_expired")

    _uset(_k_state(call_id), 8)

    events.emit(
        b"ResultRefundedExpired",
        {
            "actor": bytes(actor),
            "call_id": bytes(call_id),
        },
    )


def call_info(call_id: bytes) -> dict:
    _ensure_init()
    return {
        "state": _uget(_k_state(call_id)),
        "requester_contract": _bget(_k_requester(call_id)),
        "payer": _bget(_k_payer(call_id)),
        "model_id": _bget(_k_model_id(call_id)),
        "job_type": _bget(_k_job_type(call_id)),
        "input_ref_hash": _bget(_k_input_ref_hash(call_id)),
        "output_schema_requirement": _bget(_k_output_schema(call_id)),
        "max_budget": _uget(_k_max_budget(call_id)),
        "timeout_height": _uget(_k_timeout_height(call_id)),
        "replication": _uget(_k_replication(call_id)),
        "quorum_mode": _uget(_k_quorum_mode(call_id)),
        "quorum_required": _uget(_k_quorum_required(call_id)),
        "challenge_window": _uget(_k_challenge_window(call_id)),
        "provider_policy": _bget(_k_provider_policy(call_id)),
        "privacy_flag": _uget(_k_privacy(call_id)),
        "callback_mode": _uget(_k_callback_mode(call_id)),
        "claimed_provider": _bget(_k_claimed_provider(call_id)),
        "result_hash": _bget(_k_result_hash(call_id)),
        "result_ref": _bget(_k_result_ref(call_id)),
        "accepted_hash": _bget(_k_accepted_hash(call_id)),
        "challenge_reason": _bget(_k_challenge_reason(call_id)),
        "challenge_evidence": _bget(_k_challenge_evidence(call_id)),
    }
