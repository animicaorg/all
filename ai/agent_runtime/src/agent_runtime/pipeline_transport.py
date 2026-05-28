"""Direct worker-to-worker pipeline activation transport.

Why this exists
---------------
A pipeline-mode chat job chains N worker stages. Stage k+1 needs the
hidden-state activation produced by stage k. Two transport paths are
supported:

  1. Direct (this module): stage k+1 fetches activation bytes over
     HTTP from stage k's locally-hosted server. No node round-trip,
     no JSON-RPC base64 overhead. Right when hidden states are large
     (≥ ~100 kB) — saving an RPC round trip plus base64 inflation
     becomes meaningful.

  2. Node-proxy: stage k pushes the (base64-encoded) bytes to the node
     via aicf.pipelineSubmitStageResult. Stage k+1 pulls them via
     aicf.pipelineGetUpstreamActivation. Works through NATs and air-gaps
     because the node is always reachable. Default fallback.

Workers run the direct transport server only when they expose a
reachable endpoint (e.g. via ANIMICA_AICF_PIPELINE_PORT and an
externally-visible host). Otherwise they advertise no endpoint and
peers automatically use the node path.

Security
--------
Every payload is signed with the producer worker's wallet key. The
node attests the worker's address ↔ key binding during registration
(workerRegister is signed via JSON-RPC over TLS in production). The
consumer side verifies the signature before accepting the bytes.
Replay attacks are prevented by binding the signature to
(job_id, stage_index, sender_address, sha256(payload)).
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import logging
import os
import socketserver
import threading
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit


log = logging.getLogger("agent_runtime.pipeline_transport")


# --------------------------------------------------------------------------- #
# Payload signing                                                             #
# --------------------------------------------------------------------------- #

def compute_payload_tag(
    *,
    job_id: str,
    stage_index: int,
    sender_address: str,
    payload: bytes,
    shared_secret: bytes,
) -> str:
    """HMAC-SHA256 over the unambiguous payload context. Returned as
    lowercase hex. The shared secret is the worker's wallet key — peers
    agree on it via the node's directory (workerStatus returns the
    pubkey). For the reference impl we accept a static secret too so
    in-process tests can drive both ends.
    """
    h = hashlib.sha256()
    h.update(payload)
    payload_hash = h.digest()
    body = (
        f"{job_id}|{int(stage_index)}|{sender_address}|"
        f"{payload_hash.hex()}"
    ).encode("utf-8")
    return hmac.new(shared_secret, body, hashlib.sha256).hexdigest()


def verify_payload_tag(
    *,
    expected: str,
    job_id: str,
    stage_index: int,
    sender_address: str,
    payload: bytes,
    shared_secret: bytes,
) -> bool:
    """Constant-time HMAC verification of a payload tag produced above."""
    if not expected:
        return False
    actual = compute_payload_tag(
        job_id=job_id, stage_index=stage_index,
        sender_address=sender_address, payload=payload,
        shared_secret=shared_secret,
    )
    return hmac.compare_digest(actual.encode("ascii"), expected.encode("ascii"))


# --------------------------------------------------------------------------- #
# Server                                                                      #
# --------------------------------------------------------------------------- #

class _ActivationStore:
    """Thread-safe per-(job_id, stage_index) blob store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._blobs: Dict[Tuple[str, int], Tuple[bytes, str, str]] = {}
        # entry: (payload, tag, sender_address)

    def put(self, job_id: str, stage_index: int,
            payload: bytes, tag: str, sender_address: str) -> None:
        with self._lock:
            self._blobs[(job_id, int(stage_index))] = (payload, tag, sender_address)

    def get(self, job_id: str, stage_index: int
            ) -> Optional[Tuple[bytes, str, str]]:
        with self._lock:
            return self._blobs.get((job_id, int(stage_index)))

    def drop(self, job_id: str, stage_index: int) -> None:
        with self._lock:
            self._blobs.pop((job_id, int(stage_index)), None)


def _make_handler(
    store: _ActivationStore,
    worker_address: str,
    *,
    shared_secret_provider: Callable[[str], bytes],
):
    """Build a request handler bound to this worker's store + signer.

    `shared_secret_provider(sender_address)` returns the bytes to verify
    a payload PUT against. For PUT, sender_address is the requester
    (this server doesn't auth the requester here — production usage
    behind TLS + node auth pubkey lookup). For GET, the payload bytes
    are returned with a signature header the consumer verifies using
    the registered upstream address.
    """

    class ActivationHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):  # noqa: A003 — base class API
            # Route through python logging instead of stderr spew.
            log.debug("pipeline_transport: " + format, *args)

        def _parse_path(self) -> Optional[Tuple[str, int]]:
            # /aicf/pipeline/{job_id}/{stage_index}
            parts = self.path.lstrip("/").split("/")
            if len(parts) != 4 or parts[0] != "aicf" or parts[1] != "pipeline":
                return None
            try:
                return parts[2], int(parts[3])
            except ValueError:
                return None

        def do_GET(self) -> None:    # noqa: N802 — base class API
            target = self._parse_path()
            if target is None:
                self._send_json(404, {"error": "not_found"})
                return
            job_id, stage_index = target
            entry = store.get(job_id, stage_index)
            if entry is None:
                self._send_json(404, {"error": "no_activation",
                                       "job_id": job_id,
                                       "stage_index": stage_index})
                return
            payload, tag, sender = entry
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("X-Animica-Sender", sender)
            self.send_header("X-Animica-Tag", tag)
            self.send_header("X-Animica-Stage", str(stage_index))
            self.send_header("X-Animica-Job", job_id)
            self.end_headers()
            self.wfile.write(payload)

        def do_PUT(self) -> None:    # noqa: N802 — base class API
            target = self._parse_path()
            if target is None:
                self._send_json(404, {"error": "not_found"})
                return
            job_id, stage_index = target
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length) if length else b""
            sender = self.headers.get("X-Animica-Sender") or ""
            tag = self.headers.get("X-Animica-Tag") or ""
            if not sender or not tag:
                self._send_json(
                    400,
                    {"error": "missing_headers",
                     "hint": "X-Animica-Sender + X-Animica-Tag required"},
                )
                return
            secret = shared_secret_provider(sender)
            if not verify_payload_tag(
                expected=tag, job_id=job_id, stage_index=stage_index,
                sender_address=sender, payload=payload, shared_secret=secret,
            ):
                self._send_json(403, {"error": "bad_signature"})
                return
            store.put(job_id, stage_index, payload, tag, sender)
            self._send_json(200, {"accepted": True,
                                   "bytes": len(payload),
                                   "job_id": job_id,
                                   "stage_index": stage_index})

        def _send_json(self, code: int, body: Dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return ActivationHandler


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class PipelineTransportServer:
    """Run a local HTTP server that serves and accepts activations.

    Designed to be cheap to start (~milliseconds) and to coexist with
    the existing JSON-RPC worker loop. Workers that don't expose a port
    skip construction entirely — the chat path then falls back to the
    node-proxy transport without any code branches.
    """

    def __init__(
        self,
        *,
        worker_address: str,
        host: str = "0.0.0.0",
        port: int = 7891,
        shared_secret_provider: Callable[[str], bytes],
        external_url: Optional[str] = None,
    ) -> None:
        self.worker_address = worker_address
        self.host = host
        self.port = port
        self.store = _ActivationStore()
        self._shared_secret_provider = shared_secret_provider
        self._external_url = external_url
        self._server: Optional[_ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._server is not None:
            return
        handler_cls = _make_handler(
            self.store,
            self.worker_address,
            shared_secret_provider=self._shared_secret_provider,
        )
        self._server = _ThreadingHTTPServer((self.host, self.port), handler_cls)
        # If the caller passed port=0 (let the OS pick), reflect the
        # actual bound port back so the public URL is accurate.
        if self.port == 0:
            self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"pipeline-transport-{self.port}",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "pipeline_transport: listening on %s:%d (advertised as %s)",
            self.host, self.port, self.public_url(),
        )

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def public_url(self) -> str:
        """Externally-reachable base URL. Honours the override passed at
        construction (useful when the worker is behind a reverse proxy);
        otherwise falls back to http://<host>:<port>."""
        if self._external_url:
            return self._external_url.rstrip("/")
        host = self.host
        if host in {"0.0.0.0", "::"}:
            # Server is bound to all interfaces; consumers need a real
            # routable host. Prefer the env override the operator sets;
            # otherwise default to localhost — direct W2W won't reach
            # us from another box without a real public host.
            host = os.environ.get(
                "ANIMICA_AICF_PIPELINE_HOST", "127.0.0.1"
            )
        return f"http://{host}:{self.port}"

    # ---- producer/consumer helpers ------------------------------------- #

    def stash_local(self, *, job_id: str, stage_index: int,
                    payload: bytes, tag: str) -> None:
        """Store a payload we produced ourselves so peers can GET it.

        Used by the stage-k worker right after computing its activation:
        the bytes live in our local store; the downstream stage's GET
        hits this server and pulls them out.
        """
        self.store.put(job_id, stage_index, payload, tag, self.worker_address)


# --------------------------------------------------------------------------- #
# Consumer-side direct fetch                                                  #
# --------------------------------------------------------------------------- #

def fetch_direct(
    *,
    base_url: str,
    chunk_path_hint: str,
    job_id: str,
    stage_index: int,
    timeout_sec: float = 5.0,
) -> Optional[Tuple[bytes, str, str]]:
    """GET activation bytes from a peer worker's transport server.

    Returns ``(payload, sender_address, tag)`` on success or ``None``
    on any failure (timeout, refused, malformed). Caller is expected to
    fall back to the node-proxy path on None — direct transport is
    optional, not load-bearing.
    """
    if not base_url:
        return None
    try:
        # Concatenate the base URL with the chunk path the node told
        # us about. Path-only hint guards against base-URL drift.
        if chunk_path_hint and chunk_path_hint.startswith("/"):
            url = base_url.rstrip("/") + chunk_path_hint
        else:
            url = f"{base_url.rstrip('/')}/aicf/pipeline/{job_id}/{stage_index}"
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        import urllib.request
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            if resp.status != 200:
                return None
            payload = resp.read()
            sender = resp.headers.get("X-Animica-Sender") or ""
            tag = resp.headers.get("X-Animica-Tag") or ""
            if not sender or not tag:
                return None
            return payload, sender, tag
    except Exception as exc:    # noqa: BLE001 — fallback path absorbs this
        log.debug("pipeline_transport: direct fetch failed (%s)", exc)
        return None


__all__ = [
    "PipelineTransportServer",
    "compute_payload_tag",
    "verify_payload_tag",
    "fetch_direct",
]
