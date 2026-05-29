"""
rpc.methods.work — Animica useful-work AI layer (aicf.work.*)
=============================================================

Thin shim that mounts the eleven ``aicf.work.*`` JSON-RPC methods built by
``aicf.work.rpc.make_work_methods`` into this node's dispatcher.

The work-layer module owns:
  - the schemas (Pydantic v2, validated at the boundary)
  - the SQLite store (mirrors the TS Prisma schema 1:1)
  - the service-layer invariants (idempotency, lease expiry, no-double-payout)

This file owns nothing of those — it only:
  - opens a fresh sqlite3 connection per JSON-RPC call (SQLite isn't
    thread-safe across the FastAPI worker pool, and our schema is small
    enough that connect-per-call is cheap with WAL on)
  - translates ``WorkError`` to ``InvalidParams`` / ``InternalError`` for
    the JSON-RPC envelope.

Methods exposed (see ``aicf/work/rpc.py`` for the full list):

  aicf.work.createJob      aicf.work.heartbeatClaim
  aicf.work.getJob         aicf.work.releaseClaim
  aicf.work.listJobs       aicf.work.submitResult
  aicf.work.registerWorker aicf.work.verifyResult
  aicf.work.heartbeatWorker aicf.work.approvePayout
  aicf.work.claimNext

DB location: ``$AICF_DB_DIR/work.sqlite3`` (defaults to
``~/.animica/aicf/work.sqlite3``). The connection is short-lived per call
so the database file can be backed up / inspected externally without
coordination.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from rpc.errors import InternalError, InvalidParams
from rpc.methods import register

log = logging.getLogger("rpc.methods.work")


def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Translate WorkError → JSON-RPC error envelope.

    Inputs reach the underlying callable as keyword args because that's how
    JSON-RPC params get bound; we forward them unchanged. WorkError carries
    a stable ``code`` we don't want to lose — surface it as ``data`` on the
    RPC error so clients can branch on it.
    """
    from aicf.work.errors import WorkError

    def _handler(**params: Any) -> Any:
        try:
            return fn(**params)
        except WorkError as we:
            payload = {"code": we.code, "message": we.message}
            if we.details is not None:
                payload["details"] = we.details
            # 4xx-range errors are caller mistakes (validation, not-found,
            # state conflict). Anything else is server-side.
            if 400 <= we.http_status < 500:
                raise InvalidParams(we.message, data=payload) from we
            raise InternalError(we.message, data=payload) from we

    _handler.__name__ = getattr(fn, "__name__", "work_handler")
    _handler.__doc__ = fn.__doc__
    return _handler


def _build_service():
    """Lazy-build the WorkService so a missing pydantic doesn't break import."""
    from aicf.work.adapters import get_adapters
    from aicf.work.db import connect
    from aicf.work.rpc import WorkService

    # Per-call connection. SQLite's WAL mode makes this cheap; it also keeps
    # the dispatcher free of any "is this connection alive" plumbing.
    return WorkService(connection_factory=connect, adapters=get_adapters())


_METHOD_DESCRIPTIONS: dict[str, str] = {
    "aicf.work.createJob":        "Create a useful-work job (idempotent on idempotencyKey).",
    "aicf.work.getJob":           "Fetch a job with its tasks / claims / results / payouts.",
    "aicf.work.listJobs":         "List open or planning jobs (most recent first).",
    "aicf.work.registerWorker":   "Register or refresh a worker by (wallet, machineId).",
    "aicf.work.heartbeatWorker":  "Mark a worker as live; bumps lastSeenAt.",
    "aicf.work.claimNext":        "Lease the next eligible task to a worker.",
    "aicf.work.heartbeatClaim":   "Keep an active claim's lease alive.",
    "aicf.work.releaseClaim":     "Release a claim back to the open pool.",
    "aicf.work.submitResult":     "Submit a worker result (idempotent on resultHash).",
    "aicf.work.verifyResult":     "Record a verification verdict for a result.",
    "aicf.work.approvePayout":    "Approve + execute a payout (one per result).",
}


def _install() -> int:
    """Build the method table and register each callable. Returns count."""
    try:
        from aicf.work.rpc import make_work_methods
    except ImportError as exc:
        # The container ships aicf.work, but a stripped install might not.
        # Don't crash the whole RPC server — just log and skip.
        log.warning("aicf.work unavailable, work-layer RPC disabled: %s", exc)
        return 0

    service = _build_service()
    methods = make_work_methods(service)
    count = 0
    for name, fn in methods.items():
        try:
            register(
                name,
                _wrap(fn),
                desc=_METHOD_DESCRIPTIONS.get(name, ""),
            )
            count += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to register %s: %s", name, exc)
    log.info("Registered %d aicf.work.* methods", count)
    return count


# Import-time side effect — matches the convention of every other module
# in this package (rpc.methods.* register via decorators at import).
_install()
