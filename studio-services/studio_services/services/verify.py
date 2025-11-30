"""
Verification service: re-compile sources, compute code-hash, compare to chain, and persist.

This module exposes a single high-level function:

- verify_source_and_store(node, req) → VerifyResult

It will:
  1) Compile the submitted package (manifest + source/code bytes) using vm_py via adapters.
  2) Compute the canonical code-hash.
  3) Resolve the expected on-chain code-hash from an address or txHash (if provided).
  4) Persist a verification record (including artifact linkage when possible).
  5) Return a structured VerifyResult to the caller.

Only light orchestration lives here. All IO (RPC, storage, hashing, compile) is delegated
to adapters under studio_services.adapters and storage under studio_services.storage.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from studio_services.adapters.node_rpc import NodeRPC
from studio_services.adapters.vm_compile import (code_hash_bytes,
                                                 compile_package)
from studio_services.adapters.vm_hash import \
    artifact_digest as compute_artifact_digest
from studio_services.errors import ApiError, BadRequest
from studio_services.models.verify import (VerifyRequest, VerifyResult,
                                           VerifyStatus)
# Storage layers (the concrete APIs are kept flexible via helpers below)
from studio_services.storage import fs as storage_fs  # type: ignore
from studio_services.storage import ids as storage_ids  # type: ignore
from studio_services.storage import sqlite as storage_sqlite  # type: ignore

log = logging.getLogger(__name__)


# ---------- Introspection helpers (keep coupling loose across modules) ----------


def _pick_first_attr(obj: Any, *names: str):
    """Return the first present attribute with one of 'names' on obj; else raise."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    raise AttributeError(f"{type(obj).__name__} missing any of {names!r}")


def _resolve_expected_onchain_hash(
    node: NodeRPC, *, address: Optional[str], tx_hash: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """
    Resolve the expected on-chain code-hash using either an address or a tx hash.
    Returns (resolved_address, expected_code_hash_hex) where values may be None.
    """
    resolved_address: Optional[str] = None
    expected_hex: Optional[str] = None

    try:
        if address:
            # Prefer canonical "get_code_hash" name but support alternates.
            get_code_hash = _pick_first_attr(
                node,
                "get_code_hash",
                "get_contract_code_hash",
                "state_getCodeHash",
                "state_get_code_hash",
            )
            expected_hex = get_code_hash(address)
            resolved_address = address
        elif tx_hash:
            # Fetch receipt and infer contract/address if present
            get_receipt = _pick_first_attr(
                node, "get_transaction_receipt", "get_receipt"
            )
            r = get_receipt(tx_hash) or {}
            addr = r.get("contractAddress") or r.get("contract_address")
            if not addr:
                # Try tx lookup as a fallback to recover 'to'/'creates'
                get_tx = _pick_first_attr(
                    node, "get_transaction_by_hash", "get_transaction"
                )
                t = get_tx(tx_hash) or {}
                addr = t.get("creates") or t.get("contractAddress") or t.get("to")
            if addr:
                get_code_hash = _pick_first_attr(
                    node,
                    "get_code_hash",
                    "get_contract_code_hash",
                    "state_getCodeHash",
                    "state_get_code_hash",
                )
                expected_hex = get_code_hash(addr)
                resolved_address = addr
    except (
        Exception
    ) as e:  # Pragmatic: surface as debug; not fatal for local match-only flows
        log.warning(
            "resolve_expected_onchain_hash failed: %s",
            e,
            exc_info=log.isEnabledFor(logging.DEBUG),
        )

    return resolved_address, expected_hex


def _open_verification_store():
    """
    Obtain a verification/persistence handle from storage.sqlite.
    The exact API may vary; we adapt at call sites.
    """
    # Prefer a typed store class if available
    if hasattr(storage_sqlite, "VerificationStore"):
        return storage_sqlite.VerificationStore()  # type: ignore[attr-defined]
    if hasattr(storage_sqlite, "Store"):
        return storage_sqlite.Store()  # type: ignore[attr-defined]
    if hasattr(storage_sqlite, "Database"):
        return storage_sqlite.Database()  # type: ignore[attr-defined]
    # Fallback: the module itself may expose procedural helpers
    return storage_sqlite


def _persist_verification(
    store,
    *,
    req: VerifyRequest,
    computed_hash_hex: str,
    onchain_hash_hex: Optional[str],
    matched: bool,
    resolved_address: Optional[str],
    artifact_id: Optional[str],
    diagnostics: Optional[Dict[str, Any]],
) -> str:
    """
    Persist verification outcome; return a stable job/id string.

    We support several shapes of storage API:
    - store.record_verification(...)
    - store.insert_verification(...)
    - store.upsert_verification(...)
    - store.save_verification_result(...)

    Fields stored:
      - address (may be None)
      - tx_hash (may be None)
      - code_hash_local (hex)
      - code_hash_onchain (hex or None)
      - matched (bool)
      - artifact_id (optional)
      - diagnostics (optional JSON)
      - status (COMPLETED/SUCCESS/FAILED)
    """
    payload = {
        "address": resolved_address,
        "tx_hash": getattr(req, "tx_hash", None),
        "code_hash_local": computed_hash_hex,
        "code_hash_onchain": onchain_hash_hex,
        "matched": bool(matched),
        "artifact_id": artifact_id,
        "diagnostics": diagnostics or None,
        "status": (
            VerifyStatus.COMPLETED.value
            if hasattr(VerifyStatus, "COMPLETED")
            else "COMPLETED"
        ),
    }

    if hasattr(store, "record_verification"):
        return store.record_verification(payload)  # type: ignore
    if hasattr(store, "insert_verification"):
        return store.insert_verification(payload)  # type: ignore
    if hasattr(store, "upsert_verification"):
        return store.upsert_verification(payload)  # type: ignore
    if hasattr(store, "save_verification_result"):
        return store.save_verification_result(payload)  # type: ignore
    if hasattr(store, "persist_verification"):
        return store.persist_verification(payload)  # type: ignore

    # As a last resort, some stores may want explicit fields:
    if hasattr(store, "add_verification"):
        return store.add_verification(
            address=payload["address"],
            tx_hash=payload["tx_hash"],
            code_hash_local=payload["code_hash_local"],
            code_hash_onchain=payload["code_hash_onchain"],
            matched=payload["matched"],
            artifact_id=payload["artifact_id"],
            diagnostics=payload["diagnostics"],
            status=payload["status"],
        )  # type: ignore

    raise ApiError(
        "Verification storage API not found; expected a store with record/insert/upsert methods."
    )


def _maybe_store_artifact(
    *, manifest: Dict[str, Any], source: Optional[str], code_bytes: Optional[bytes]
) -> Optional[str]:
    """
    Optionally create a content-addressed artifact in storage.fs and return its id.

    We tolerate missing storage.fs capabilities gracefully (return None).
    """
    try:
        artifact_id = storage_ids.make_artifact_id(manifest=manifest, source=source, code_bytes=code_bytes)  # type: ignore[attr-defined]
        # Choose the most specific 'put' primitive available
        if hasattr(storage_fs, "put_artifact"):
            storage_fs.put_artifact(artifact_id, manifest=manifest, source=source, code_bytes=code_bytes)  # type: ignore
        elif hasattr(storage_fs, "store_artifact"):
            storage_fs.store_artifact(artifact_id, manifest=manifest, source=source, code_bytes=code_bytes)  # type: ignore
        elif hasattr(storage_fs, "write_artifact"):
            storage_fs.write_artifact(artifact_id, manifest=manifest, source=source, code_bytes=code_bytes)  # type: ignore
        else:
            # Best-effort: allow storage.fs to expose a generic "put_blob" that takes bytes
            if hasattr(storage_fs, "put_blob"):
                blob = {
                    "manifest": manifest,
                    "source": source,
                    "code_bytes_hex": (
                        code_bytes.hex()
                        if isinstance(code_bytes, (bytes, bytearray))
                        else None
                    ),
                }
                storage_fs.put_blob(artifact_id, blob)  # type: ignore
        return artifact_id
    except Exception as e:
        log.warning(
            "Artifact store skipped: %s", e, exc_info=log.isEnabledFor(logging.DEBUG)
        )
        return None


# ---------- Public API ----------


def verify_source_and_store(
    node: NodeRPC,
    req: VerifyRequest,
) -> VerifyResult:
    """
    Compile sources, compute code-hash, compare to chain, persist & return result.

    Parameters
    ----------
    node : NodeRPC
        RPC adapter used to fetch on-chain state (code-hash by address/tx).
    req : VerifyRequest
        Model containing manifest + source/code, and optional address/tx hash to match.

    Returns
    -------
    VerifyResult
    """
    # Basic input validation
    if req.manifest is None:
        raise BadRequest("manifest is required")
    if req.source is None and req.code_bytes is None:
        raise BadRequest("one of source or code_bytes is required")

    # 1) Compile/build using vm_py (via adapter)
    build = compile_package(
        manifest=req.manifest,
        source=req.source,
        code_bytes=req.code_bytes,
    )

    # 2) Compute canonical code-hash (hex, 0x-prefixed)
    ch_bytes = code_hash_bytes(build)
    computed_code_hash_hex = "0x" + ch_bytes.hex()

    # 3) Resolve expected hash from chain if possible
    resolved_address, expected_onchain = _resolve_expected_onchain_hash(
        node, address=req.address, tx_hash=getattr(req, "tx_hash", None)
    )

    matched = (expected_onchain is not None) and (
        computed_code_hash_hex.lower() == str(expected_onchain).lower()
    )

    # 4) Persist artifacts (optional) + verification record
    artifact_id: Optional[str] = None
    if getattr(req, "store_artifact", False):
        artifact_id = _maybe_store_artifact(
            manifest=req.manifest,
            source=req.source,
            code_bytes=req.code_bytes,
        )

    store = _open_verification_store()
    job_id = _persist_verification(
        store,
        req=req,
        computed_hash_hex=computed_code_hash_hex,
        onchain_hash_hex=expected_onchain,
        matched=matched,
        resolved_address=resolved_address,
        artifact_id=artifact_id,
        diagnostics=build.diagnostics or None,
    )

    # 5) Return structured result
    status = (
        VerifyStatus.SUCCESS
        if matched
        else VerifyStatus.MISMATCH if expected_onchain else VerifyStatus.LOCAL_ONLY
    )
    artifact_digest = None
    try:
        artifact_digest = compute_artifact_digest(
            manifest=req.manifest, source=req.source, code_bytes=req.code_bytes
        )
    except Exception:  # non-fatal
        pass

    return VerifyResult(
        job_id=job_id,
        status=status,
        address=resolved_address or req.address,
        tx_hash=getattr(req, "tx_hash", None),
        code_hash_local=computed_code_hash_hex,
        code_hash_onchain=expected_onchain,
        matched=matched,
        artifact_id=artifact_id,
        artifact_digest=artifact_digest,
        diagnostics=build.diagnostics or None,
    )


def submit_verify(req: VerifyRequest) -> VerifyResult:
    """Compatibility wrapper for router discovery."""

    node = from_env()
    return verify_source_and_store(node, req)


def get_verify_by_address(address: str) -> VerifyResult:
    """Best-effort lookup placeholder when persistent store is unavailable."""

    try:
        from studio_services.config import load_config

        chain_id = getattr(load_config(), "CHAIN_ID", 0)
    except Exception:
        chain_id = 0

    return VerifyResult(
        job_id="unknown",
        status=VerifyStatus.LOCAL_ONLY,
        matched=False,
        chain_id=chain_id,
        address=address,
        tx_hash=None,
        computed_code_hash="0x",
        expected_code_hash=None,
        abi={},
        diagnostics=[],
        error="verification storage not available",
    )


def get_verify_by_txhash(tx_hash: str) -> VerifyResult:
    try:
        from studio_services.config import load_config

        chain_id = getattr(load_config(), "CHAIN_ID", 0)
    except Exception:
        chain_id = 0

    return VerifyResult(
        job_id="unknown",
        status=VerifyStatus.LOCAL_ONLY,
        matched=False,
        chain_id=chain_id,
        address=None,
        tx_hash=tx_hash,
        computed_code_hash="0x",
        expected_code_hash=None,
        abi={},
        diagnostics=[],
        error="verification storage not available",
    )


__all__ = [
    "verify_source_and_store",
    "submit_verify",
    "get_verify_by_address",
    "get_verify_by_txhash",
]
