from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, TypedDict, Union

try:
    # stdlib-only HTTP client (no external deps)
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError
except Exception:  # pragma: no cover
    # This will basically never happen, but keeps type-checkers happy.
    urlopen = None  # type: ignore


Json = Union[dict, list, str, int, float, None]


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"RPC error {code}: {message}")
        self.code = code
        self.data = data


class TransportError(Exception):
    pass


class SubmitRejected(Exception):
    """Raised when the node explicitly rejects a share/block (semantic failure)."""


@dataclass
class SubmitterConfig:
    rpc_url: str = "http://127.0.0.1:8545/rpc"
    timeout_s: float = 5.0
    max_retries: int = 5
    initial_backoff_s: float = 0.25
    max_backoff_s: float = 5.0
    jitter: float = 0.25  # 0..1 proportion of backoff to add/sub as jitter
    batch_size: int = 256
    http_headers: Dict[str, str] = field(default_factory=lambda: {"Content-Type": "application/json"})
    method_submit_share: str = "miner.submitShare"
    method_submit_share_batch: str = "miner.submitShareBatch"
    method_submit_block: str = "miner.submitBlock"


class ShareResult(TypedDict, total=False):
    accepted: bool
    reason: str
    hash: str
    d_ratio: float
    height: int


@dataclass
class SubmitStats:
    shares_accepted: int = 0
    shares_rejected: int = 0
    shares_errors: int = 0
    blocks_accepted: int = 0
    blocks_rejected: int = 0
    last_error: Optional[str] = None

    def snapshot(self) -> "SubmitStats":
        return SubmitStats(
            shares_accepted=self.shares_accepted,
            shares_rejected=self.shares_rejected,
            shares_errors=self.shares_errors,
            blocks_accepted=self.blocks_accepted,
            blocks_rejected=self.blocks_rejected,
            last_error=self.last_error,
        )


def _default_share_encoder(share: Any) -> Dict[str, Any]:
    """
    Try hard to convert a FoundShare-like object into a JSON-RPC payload.

    Expected keys (best-effort):
      - header: dict or bytes(hex)
      - nonce: int or hex string
      - mixSeed: bytes(hex) or omitted
      - proof: dict (HashShare envelope) or fields under "hashshare"
      - d_ratio: float (share difficulty ratio)
      - height: int (template height)

    We support:
      - dataclass with asdict()
      - object with .to_dict()
      - mapping
    """
    # Mapping already?
    if isinstance(share, dict):
        m = dict(share)
    else:
        # dataclass?
        try:
            m = asdict(share)  # type: ignore[arg-type]
        except Exception:
            # generic "to_dict"
            if hasattr(share, "to_dict"):
                m = share.to_dict()  # type: ignore[attr-defined]
            else:
                # As a last resort, introspect attributes
                m = {k: getattr(share, k) for k in dir(share) if not k.startswith("_")}

    # Normalize common keys/casing
    payload: Dict[str, Any] = {}
    # Header/template
    header = m.get("header") or m.get("header_template") or m.get("candidate_header")
    if header is None:
        raise ValueError("share encoder: missing 'header'")
    payload["header"] = header

    # Nonce / mixSeed
    nonce = m.get("nonce") or m.get("nonce64") or m.get("n")
    if nonce is None:
        raise ValueError("share encoder: missing 'nonce'")
    payload["nonce"] = nonce

    mix = m.get("mix_seed") or m.get("mixSeed") or m.get("mix")
    if mix is not None:
        payload["mixSeed"] = mix

    # Proof (HashShare)
    proof = (
        m.get("proof")
        or m.get("hashshare")
        or m.get("hash_share")
        or m.get("hashShare")
        or m.get("proof_envelope")
    )
    if proof is None:
        raise ValueError("share encoder: missing 'proof' (HashShare envelope)")
    payload["proof"] = proof

    # Optional hints
    if "d_ratio" in m:
        payload["d_ratio"] = m["d_ratio"]
    if "height" in m:
        payload["height"] = m["height"]

    return payload


def _default_block_encoder(candidate_block: Any) -> Dict[str, Any]:
    """
    Convert a candidate Block object/dict to the RPC payload expected by miner.submitBlock.
    Minimal contract: {'header': {...}, 'txs': [...], 'proofs': [...]}.
    """
    if isinstance(candidate_block, dict):
        b = dict(candidate_block)
    else:
        try:
            b = asdict(candidate_block)  # type: ignore[arg-type]
        except Exception:
            if hasattr(candidate_block, "to_dict"):
                b = candidate_block.to_dict()  # type: ignore[attr-defined]
            else:
                b = {k: getattr(candidate_block, k) for k in dir(candidate_block) if not k.startswith("_")}

    for key in ("header", "txs", "proofs"):
        if key not in b:
            raise ValueError(f"block encoder: missing '{key}'")
    return {"header": b["header"], "txs": b["txs"], "proofs": b["proofs"]}


class JsonRpcClient:
    def __init__(self, url: str, headers: Optional[Dict[str, str]] = None, timeout_s: float = 5.0):
        self._url = url
        self._headers = headers or {"Content-Type": "application/json"}
        self._timeout_s = timeout_s
        self._id = 0
        self._log = logging.getLogger("mining.share_submitter.rpc")

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def call(self, method: str, params: Any) -> Any:
        req = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params}
        body = json.dumps(req).encode("utf-8")
        r = Request(self._url, data=body, headers=self._headers, method="POST")
        try:
            with urlopen(r, timeout=self._timeout_s) as resp:
                content = resp.read()
        except HTTPError as e:
            raise TransportError(f"HTTP {e.code}: {e.reason}") from e
        except URLError as e:
            raise TransportError(str(e)) from e
        except Exception as e:
            raise TransportError(repr(e)) from e

        try:
            obj = json.loads(content)
        except Exception as e:
            raise TransportError(f"Invalid JSON from RPC: {e}")

        if "error" in obj and obj["error"]:
            err = obj["error"]
            raise RpcError(err.get("code", -32000), err.get("message", "Unknown error"), err.get("data"))
        return obj.get("result")

    def batch(self, calls: List[Tuple[str, Any]]) -> List[Any]:
        batch_req = [
            {"jsonrpc": "2.0", "id": self._next_id(), "method": m, "params": p} for (m, p) in calls
        ]
        body = json.dumps(batch_req).encode("utf-8")
        r = Request(self._url, data=body, headers=self._headers, method="POST")
        try:
            with urlopen(r, timeout=self._timeout_s) as resp:
                content = resp.read()
        except HTTPError as e:
            raise TransportError(f"HTTP {e.code}: {e.reason}") from e
        except URLError as e:
            raise TransportError(str(e)) from e
        except Exception as e:
            raise TransportError(repr(e)) from e

        try:
            arr = json.loads(content)
        except Exception as e:
            raise TransportError(f"Invalid JSON from RPC: {e}")

        # Map id -> result/error; then reorder to match input order
        by_id = {item["id"]: item for item in arr}
        out: List[Any] = []
        for req in batch_req:
            item = by_id.get(req["id"])
            if item is None:
                out.append(RpcError(-32000, "Missing response item"))
                continue
            if "error" in item and item["error"]:
                err = item["error"]
                out.append(RpcError(err.get("code", -32000), err.get("message", "Unknown error"), err.get("data")))
            else:
                out.append(item.get("result"))
        return out


class ShareSubmitter:
    """
    Consumes FoundShare objects, submits them to the local node via JSON-RPC with
    retries/backoff, and records basic stats.

    It first attempts "miner.submitShareBatch" for efficiency; if the node returns
    -32601 (Method not found), it falls back to one-by-one "miner.submitShare".
    """

    def __init__(
        self,
        cfg: SubmitterConfig,
        *,
        share_encoder: Callable[[Any], Dict[str, Any]] = _default_share_encoder,
        block_encoder: Callable[[Any], Dict[str, Any]] = _default_block_encoder,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.cfg = cfg
        self.rpc = JsonRpcClient(cfg.rpc_url, cfg.http_headers, cfg.timeout_s)
        self._share_encoder = share_encoder
        self._block_encoder = block_encoder
        self._log = logger or logging.getLogger("mining.share_submitter")
        self._stats = SubmitStats()
        self._closed = False
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

    # ──────────────────────────────────────────────────────────────────────
    # Public stats/lifecycle
    # ──────────────────────────────────────────────────────────────────────

    def stats(self) -> SubmitStats:
        return self._stats.snapshot()

    def close(self) -> None:
        self._closed = True
        self._stop_evt.set()

    # ──────────────────────────────────────────────────────────────────────
    # Single-shot APIs (usable without the background consumer)
    # ──────────────────────────────────────────────────────────────────────

    def submit_share_once(self, share: Any) -> ShareResult:
        payload = self._share_encoder(share)
        backoff = self.cfg.initial_backoff_s
        tries = 0
        while True:
            tries += 1
            try:
                res = self.rpc.call(self.cfg.method_submit_share, [payload])
                # Result contract (recommended): {'accepted': bool, 'reason': str, 'hash': '0x…', ...}
                if isinstance(res, dict):
                    accepted = bool(res.get("accepted", False))
                    if accepted:
                        self._stats.shares_accepted += 1
                    else:
                        self._stats.shares_rejected += 1
                    return ShareResult(
                        accepted=accepted,
                        reason=str(res.get("reason", "")),
                        hash=str(res.get("hash", "")),
                        d_ratio=float(res.get("d_ratio", payload.get("d_ratio") or 0.0)),
                        height=int(res.get("height", payload.get("height") or 0)),
                    )
                # Fallback: some nodes may return True/False
                if res is True:
                    self._stats.shares_accepted += 1
                    return ShareResult(accepted=True)
                self._stats.shares_rejected += 1
                return ShareResult(accepted=False, reason="unexpected-result")
            except RpcError as e:
                # If method not found, caller might support batch only or a different mount.
                if e.code == -32601 and tries == 1:
                    # Try batch form with a single element
                    try:
                        br = self.rpc.call(self.cfg.method_submit_share_batch, [[payload]])
                        # Expect a list of ShareResult
                        if isinstance(br, list) and br:
                            out = br[0]
                            if isinstance(out, dict):
                                accepted = bool(out.get("accepted", False))
                                if accepted:
                                    self._stats.shares_accepted += 1
                                else:
                                    self._stats.shares_rejected += 1
                                return ShareResult(
                                    accepted=accepted,
                                    reason=str(out.get("reason", "")),
                                    hash=str(out.get("hash", "")),
                                    d_ratio=float(out.get("d_ratio", payload.get("d_ratio") or 0.0)),
                                    height=int(out.get("height", payload.get("height") or 0)),
                                )
                            if out is True:
                                self._stats.shares_accepted += 1
                                return ShareResult(accepted=True)
                            self._stats.shares_rejected += 1
                            return ShareResult(accepted=False, reason="unexpected-batch-result")
                    except RpcError as e2:
                        # keep falling through to retry logic
                        self._stats.last_error = f"{e2.code}:{e2}"
                # Semantic rejections should not be retried aggressively
                if e.code in (-32010, -32011, -32012):  # examples: WorkExpired, LowDifficulty, Duplicate
                    self._stats.shares_rejected += 1
                    return ShareResult(accepted=False, reason=f"rpc:{e.code}:{e}")
                # Transport-level or transient server errors: backoff
                self._stats.shares_errors += 1
                self._stats.last_error = f"{e.code}:{e}"
            except TransportError as e:
                self._stats.shares_errors += 1
                self._stats.last_error = str(e)

            if tries >= self.cfg.max_retries:
                return ShareResult(accepted=False, reason=f"retries-exhausted:{self._stats.last_error or ''}")

            # Exponential backoff with jitter
            sleep = backoff * (1.0 + (random.random() * 2 - 1) * self.cfg.jitter)
            sleep = max(0.0, min(sleep, self.cfg.max_backoff_s))
            self._log.debug("share submit retry", tries=tries, sleep=sleep, last_error=self._stats.last_error)
            time.sleep(sleep)
            backoff = min(backoff * 2.0, self.cfg.max_backoff_s)

    def submit_block_once(self, candidate_block: Any) -> Dict[str, Any]:
        payload = self._block_encoder(candidate_block)
        backoff = self.cfg.initial_backoff_s
        tries = 0
        while True:
            tries += 1
            try:
                res = self.rpc.call(self.cfg.method_submit_block, [payload])
                # Contract: {'accepted': bool, 'reason': str, 'hash': '0x…', 'height': N}
                if isinstance(res, dict):
                    accepted = bool(res.get("accepted", False))
                    if accepted:
                        self._stats.blocks_accepted += 1
                    else:
                        self._stats.blocks_rejected += 1
                    return res
                # Fallback
                if res is True:
                    self._stats.blocks_accepted += 1
                    return {"accepted": True}
                self._stats.blocks_rejected += 1
                return {"accepted": False, "reason": "unexpected-result"}
            except RpcError as e:
                if e.code in (-32020, -32021, -32022):  # e.g., InvalidBlock, BadProofs, Stale
                    self._stats.blocks_rejected += 1
                    return {"accepted": False, "reason": f"rpc:{e.code}:{e}"}
                self._stats.last_error = f"{e.code}:{e}"
            except TransportError as e:
                self._stats.last_error = str(e)

            if tries >= self.cfg.max_retries:
                return {"accepted": False, "reason": f"retries-exhausted:{self._stats.last_error or ''}"}

            sleep = backoff * (1.0 + (random.random() * 2 - 1) * self.cfg.jitter)
            sleep = max(0.0, min(sleep, self.cfg.max_backoff_s))
            self._log.debug("block submit retry", tries=tries, sleep=sleep, last_error=self._stats.last_error)
            time.sleep(sleep)
            backoff = min(backoff * 2.0, self.cfg.max_backoff_s)

    # ──────────────────────────────────────────────────────────────────────
    # Background consumer from a ShareBuffer
    # ──────────────────────────────────────────────────────────────────────

    def start_consumer(
        self,
        buffer: "ShareBufferLike",
        *,
        name: str = "ShareSubmitter",
        max_items_per_batch: Optional[int] = None,
        poll_timeout_s: float = 0.05,
    ) -> None:
        """
        Start a background thread that continuously drains from `buffer` and submits shares.

        `buffer` must expose:
            - pop_batch(max_items: int, timeout: float) -> list[Any]
            - stats() -> object (optional, only for debug logs)
        """
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        batch = max_items_per_batch or self.cfg.batch_size

        def _run() -> None:
            self._log.info("share submitter started", url=self.cfg.rpc_url, batch=batch)
            while not self._stop_evt.is_set():
                try:
                    shares = buffer.pop_batch(max_items=batch, timeout=poll_timeout_s)
                except Exception as e:  # defensive; buffer should not raise
                    self._log.exception("buffer.pop_batch failed: %r", e)
                    time.sleep(0.1)
                    continue
                if not shares:
                    continue
                # Try batch submit first; fall back to one-by-one if unsupported
                encoded = []
                try:
                    for s in shares:
                        encoded.append(self._share_encoder(s))
                except Exception as e:
                    # If any share can't be encoded, fall back to per-share processing
                    self._log.warning("share encoder error; falling back per-share: %r", e)
                    encoded = []

                if encoded:
                    try:
                        results = self._submit_batch(encoded)
                        # Update stats from results
                        for r in results:
                            if isinstance(r, dict) and r.get("accepted") is True:
                                self._stats.shares_accepted += 1
                            elif r is True:
                                self._stats.shares_accepted += 1
                            else:
                                self._stats.shares_rejected += 1
                        continue
                    except RpcError as e:
                        if e.code != -32601:
                            # Batch supported but failed; treat as transient and retry individually
                            self._log.debug("batch submit failed; falling back per-share: %s", e)
                        # else: method not found → permanent fallback below
                    except TransportError as e:
                        self._log.debug("transport error on batch; falling back per-share: %s", e)
                        # fall through to per-share loop

                # Per-share submissions with individual retries
                for s in shares:
                    try:
                        res = self.submit_share_once(s)
                        # (stats already updated in submit_share_once)
                        if not res.get("accepted", False):
                            self._log.debug("share rejected", reason=res.get("reason", ""))
                    except Exception as e:
                        self._stats.shares_errors += 1
                        self._stats.last_error = repr(e)
                        self._log.debug("share submit error: %s", e)

            self._log.info("share submitter stopped")

        self._thread = threading.Thread(target=_run, name=name, daemon=True)
        self._thread.start()

    def stop_consumer(self, join: bool = True, timeout: Optional[float] = 5.0) -> None:
        self._stop_evt.set()
        if self._thread and join:
            self._thread.join(timeout=timeout)

    # ──────────────────────────────────────────────────────────────────────
    # Internals
    # ──────────────────────────────────────────────────────────────────────

    def _submit_batch(self, encoded_shares: List[Dict[str, Any]]) -> List[Any]:
        """
        Attempt batch submit via miner.submitShareBatch. Raises RpcError/TransportError on failure.
        """
        return self.rpc.call(self.cfg.method_submit_share_batch, [encoded_shares])


# Protocol (duck type) for ShareBuffer-like objects
class ShareBufferLike:
    def pop_batch(self, max_items: int = 1024, timeout: float = 0.0) -> List[Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def stats(self) -> Any:  # pragma: no cover - interface
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# CLI for quick manual testing
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Submit a single share or block to the local node RPC.")
    ap.add_argument("--rpc", default="http://127.0.0.1:8545/rpc", help="JSON-RPC endpoint")
    ap.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout (s)")
    ap.add_argument("--headers", type=str, default="", help='Extra headers JSON, e.g. \'{"Authorization":"Bearer …"}\'')
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp1 = sub.add_parser("share", help="Submit a single share JSON file")
    sp1.add_argument("--file", required=True, help="Path to share JSON (payload with header/nonce/proof)")

    sp2 = sub.add_parser("block", help="Submit a candidate block JSON file")
    sp2.add_argument("--file", required=True, help="Path to block JSON (header/txs/proofs)")

    args = ap.parse_args()
    headers = {}
    if args.headers:
        try:
            headers = json.loads(args.headers)
        except Exception as e:
            print(f"Invalid headers JSON: {e}", file=sys.stderr)
            sys.exit(2)

    cfg = SubmitterConfig(rpc_url=args.rpc, timeout_s=args.timeout, http_headers=headers)
    subm = ShareSubmitter(cfg)

    try:
        with open(args.file, "rb") as fh:
            obj = json.loads(fh.read())
    except Exception as e:
        print(f"Failed to read JSON: {e}", file=sys.stderr)
        sys.exit(2)

    if args.cmd == "share":
        res = subm.submit_share_once(obj)
        print(json.dumps(res, indent=2))
    else:
        res = subm.submit_block_once(obj)
        print(json.dumps(res, indent=2))
