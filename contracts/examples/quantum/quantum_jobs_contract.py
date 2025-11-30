# QuantumJobs contract skeleton
# Manages job submission, result submission, escrow and payouts

from stdlib import abi, events, pq_verify, storage

K_INIT = b"qj/init"
K_JOB_PREFIX = b"qj/job/"  # + job_id -> struct
K_JOB_SEQ = b"qj/seq"
K_TOKEN_CONTRACT = b"qj/token_contract"

# Helpers


def _job_key(job_id: bytes) -> bytes:
    return K_JOB_PREFIX + job_id


def _get_uint(k):
    v = storage.get(k)
    if v is None:
        return 0
    if not isinstance(v, int):
        abi.revert(b"ERR_TYPE_UINT")
    return v


def _set_uint(k, v):
    storage.set(k, int(v))


# Views


def get_job(job_id: bytes) -> dict:
    j = storage.get(_job_key(job_id))
    if j is None:
        return {"exists": False}
    return j


# Submit job: job_spec is a bytes/CBOR canonicalized blob


def submit_job(job_id: bytes, job_spec: bytes, fee_escrow: int) -> None:
    # Basic checks
    if storage.get(_job_key(job_id)) is not None:
        abi.revert(b"ERR_JOB_EXISTS")

    # TODO: decode/validate job_spec against schema (off-chain / VM lightweight checks)

    storage.set(
        _job_key(job_id),
        {
            "owner": abi.caller(),
            "spec": job_spec,
            "status": b"open",
            "escrow": fee_escrow,
            "submissions": [],
        },
    )
    events.emit(b"JobSubmitted", [job_id, abi.caller()])


# Submit result


def submit_result(
    job_id: bytes, result_commitment: bytes, worker_id: bytes, worker_signature: bytes
) -> None:
    j = storage.get(_job_key(job_id))
    if j is None:
        abi.revert(b"ERR_UNKNOWN_JOB")
    if j.get("status") != b"open":
        abi.revert(b"ERR_JOB_NOT_OPEN")

    # Verify worker registered and not slashed via QuantumWorkers contract (cross-contract call)
    # For development we expect the worker pubkey to be stored in QuantumWorkers as hex of the HMAC key
    # Retrieve worker pubkey from the worker registry (simple cross-contract pattern assumed)
    try:
        # Example cross-contract call pattern; adapt to your VM's cross-call API if different
        worker_info_raw = abi.call_contract(
            b"QuantumWorkers", b"get_worker", {"worker_id": worker_id}
        )
        # worker_info_raw is expected to be an encoded struct; try to decode lightly
        # For this skeleton we assume it returns a dict-like object
        worker_pubkey = (
            worker_info_raw.get("pubkey") if isinstance(worker_info_raw, dict) else None
        )
    except Exception:
        worker_pubkey = None

    if not worker_pubkey:
        abi.revert(b"ERR_UNKNOWN_WORKER")

    # Build canonical bytes for signature verification
    # Canonicalization should be upgraded to CBOR/json canonical helpers; here we use a simple delimiter
    sign_target = job_id + b"|" + result_commitment

    # call into stdlib pq_verify (development shim)
    ok = pq_verify.verify(worker_pubkey, sign_target, worker_signature)
    if not ok:
        abi.revert(b"ERR_BAD_SIGNATURE")

    # Record submission
    subs = j.get("submissions", [])
    subs.append(
        {
            "worker_id": worker_id,
            "result_commitment": result_commitment,
            "signature": worker_signature,
            "status": b"submitted",
        }
    )
    j["submissions"] = subs
    storage.set(_job_key(job_id), j)

    events.emit(b"ResultSubmitted", [job_id, worker_id, result_commitment])

    # Basic immediate acceptance path (Stage 1): accept if worker registered and signature OK
    # For Stage 2/3, adopt committee or proof verification flow
    # Basic immediate acceptance path (Stage 1): accept if worker registered and signature OK
    if ok:
        # mark job completed and request payout
        j["status"] = b"completed"
        storage.set(_job_key(job_id), j)

        # Emit JobCompleted and PayoutRequested events. Off-chain relayer or treasury
        # service should perform the actual token transfer from the escrow to the worker
        # using the configured token contract.
        events.emit(b"JobCompleted", [job_id, worker_id, result_commitment])
        # payout event: [job_id, worker_id, amount (int), token_contract (bytes)]
        token_addr = storage.get(K_TOKEN_CONTRACT) or b""
        events.emit(
            b"PayoutRequested", [job_id, worker_id, j.get("escrow", 0), token_addr]
        )
        # zero out escrow in storage
        j["escrow"] = 0
        storage.set(_job_key(job_id), j)
        return

    # If not ok, leave submission recorded for dispute/committee flow
    return


# Dispute path (placeholder)


def dispute_result(job_id: bytes, evidence: bytes) -> None:
    # TODO: implement dispute logic (challenge window, committee arbitration)
    events.emit(b"ResultDisputed", [job_id, abi.caller()])


# Main dispatcher


def main(action: bytes, **kwargs) -> bytes:
    if action == b"submit_job":
        submit_job(kwargs["job_id"], kwargs["job_spec"], kwargs.get("fee_escrow", 0))
        return b""
    if action == b"submit_result":
        submit_result(
            kwargs["job_id"],
            kwargs["result_commitment"],
            kwargs["worker_id"],
            kwargs["worker_signature"],
        )
        return b""
    if action == b"dispute_result":
        dispute_result(kwargs["job_id"], kwargs.get("evidence", b""))
        return b""
    if action == b"get_job":
        return abi.encode_struct(get_job(kwargs["job_id"]))
    abi.revert(b"ERR_UNKNOWN_ACTION")
