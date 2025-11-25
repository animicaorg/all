"""
Oracle DA Poster — main entrypoint

This module wires together:
  - Feed -> produces a fresh payload (bytes) and metadata for an oracle update
  - DA client -> posts payload to the DA layer and returns a commitment
  - Tx client -> submits a contract call to the on-chain oracle with that commitment

It is intentionally stdlib-only and depends on sibling modules inside this
template. See README for environment variables. Typical run-modes:

  # one-shot (fetch -> post -> submit -> wait receipt)
  python -m oracle_poster.main --once

  # daemon mode (loop forever)
  python -m oracle_poster.main

Flags allow dry-run, selectively skipping DA or TX, and printing derived args.

Security note:
- By default, signing is delegated to a "services" backend using a signer label.
- For dev/testing you can switch to raw signer mode (see PosterEnv.signer_mode).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Local imports (kept flexible to avoid import churn across template evolution)
try:
    # Preferred exports from __init__.py
    from . import PosterEnv, get_logger, load_env
except Exception:  # pragma: no cover - fallback to config.py
    from .config import PosterEnv, load_env  # type: ignore
    from . import get_logger  # type: ignore

# Feeds & clients
from .feeds import load_feed  # Factory returning an object with .produce() -> (bytes, dict)
from .da_client import DAClient  # Exposes .post(data: bytes, **kw) -> DAResult
from .tx_client import TxClient, TxReceipt

LOG = get_logger("oracle_poster.main")


# ======================================================================================
# Helpers
# ======================================================================================

def _bool_env(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default


def _flatten_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort JSON-serializable copy for logs/prints."""
    def _coerce(x: Any) -> Any:
        if isinstance(x, (str, int, float, bool)) or x is None:
            return x
        if isinstance(x, bytes):
            # avoid dumping huge blobs in logs
            n = min(16, len(x))
            return f"bytes<{len(x)}:{x[:n].hex()}...>"
        if isinstance(x, dict):
            return {str(k): _coerce(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [_coerce(v) for v in x]
        return repr(x)
    return _coerce(meta)  # type: ignore[return-value]


def _map_oracle_args(order: Iterable[str], *, commitment: str, size: int, meta: Dict[str, Any]) -> List[Any]:
    """
    Build the contract-call arguments according to a configured order.
    Supported tokens (case-insensitive):
      - commitment  : hex 0x... (bytes32)
      - size        : integer size of payload in bytes
      - feed_tag    : string tag/name for the feed (from meta['feed_tag'])
      - mime        : content type (from meta['mime'])
      - ts          : unix timestamp seconds when produced (int)
      - digest      : optional secondary digest hex (meta['digest'])
      - extra_<k>   : any meta['k'] accessible by prefix 'extra_'
    """
    meta_l = {k.lower(): v for k, v in meta.items()}
    out: List[Any] = []
    now_ts = int(meta.get("ts") or time.time())
    for token in order:
        t = token.strip().lower()
        if t == "commitment":
            out.append(commitment)
        elif t == "size":
            out.append(int(size))
        elif t == "feed_tag":
            out.append(meta.get("feed_tag") or meta_l.get("tag") or "")
        elif t == "mime":
            out.append(meta.get("mime") or meta_l.get("content_type") or "application/octet-stream")
        elif t == "ts":
            out.append(int(now_ts))
        elif t == "digest":
            out.append(meta.get("digest") or meta_l.get("digest") or None)
        elif t.startswith("extra_"):
            key = t[len("extra_") :]
            out.append(meta.get(key) or meta_l.get(key))
        else:
            # Pass-through literal values if token is quoted like "'literal'"
            if len(t) >= 2 and ((t[0] == t[-1] == "'") or (t[0] == t[-1] == '"')):
                out.append(token[1:-1])
            else:
                raise ValueError(f"Unknown oracle arg token: {token!r}")
    return out


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


# ======================================================================================
# Core flow
# ======================================================================================

def run_once(
    cfg: PosterEnv,
    *,
    feed_name: Optional[str] = None,
    dry_run: bool = False,
    skip_da: bool = False,
    skip_tx: bool = False,
    print_args: bool = False,
    wait_receipt: Optional[bool] = None,
) -> Tuple[Optional[str], Optional[TxReceipt]]:
    """
    Executes exactly one iteration:
      1) Ask feed for payload
      2) Optionally post to DA
      3) Optionally submit TX to oracle

    Returns (tx_hash, receipt_or_none)
    """
    feed_id = feed_name or cfg.feed_name
    if not feed_id:
        raise RuntimeError("No feed_name configured or passed on CLI")

    # 1) FEED
    feed = load_feed(cfg, feed_id)
    payload, meta = feed.produce()
    meta = dict(meta or {})
    meta.setdefault("feed_tag", feed_id)
    meta.setdefault("ts", int(time.time()))
    size = len(payload or b"")
    LOG.info("Feed produced %d bytes (feed=%s mime=%s)", size, feed_id, meta.get("mime"))

    # In dry-run mode we still compute args to show what *would* be sent.
    commitment_hex: Optional[str] = None

    # 2) DA
    if not skip_da:
        da = DAClient(cfg)
        res = da.post(payload, meta=meta)
        commitment_hex = res.commitment
        LOG.info("DA posted: size=%d commitment=%s", res.size, commitment_hex)
        # Let feeds augment meta with DA result if they want to (duck-typed)
        try:
            feed.on_da_result(res)  # type: ignore[attr-defined]
        except AttributeError:
            pass
        meta.setdefault("commitment", commitment_hex)
        meta.setdefault("da_namespace", getattr(res, "namespace", None))
        meta.setdefault("da_uri", getattr(res, "uri", None))
    else:
        LOG.warning("Skipping DA post (--no-da). Commitment will be None.")
        size = len(payload)

    # 3) TX
    if skip_tx:
        LOG.warning("Skipping TX submit (--no-tx).")
        tx_hash = None
        receipt = None
    else:
        if wait_receipt is None:
            wait_receipt = bool(getattr(cfg, "tx_wait_receipt", True))

        # Derive commitment if caller bypassed DA (allows pre-committed payload)
        if commitment_hex is None:
            # If the feed provides its own commitment/digest, prefer that
            commitment_hex = meta.get("commitment")
            if not commitment_hex:
                # Try to compute via DA client utility (without post)
                da = DAClient(cfg)
                commitment_hex = da.compute_commitment(payload)
                LOG.info("Computed commitment locally: %s", commitment_hex)

        # Build method + args
        method = (cfg.oracle_method or "set(bytes32,uint256)").strip()
        order = list(cfg.oracle_arg_order or ("commitment", "size"))
        args = _map_oracle_args(order, commitment=str(commitment_hex), size=size, meta=meta)

        if print_args or dry_run:
            _print_json({"method": method, "args": args, "meta": _flatten_meta(meta)})

        if dry_run:
            LOG.info("Dry-run enabled; not submitting transaction.")
            tx_hash = None
            receipt = None
        else:
            txc = TxClient(cfg)
            submit, receipt = txc.send_contract_call(
                to=cfg.oracle_contract,
                method=method,
                args=args,
                from_addr=getattr(cfg, "from_address", None),
                gas_limit=getattr(cfg, "default_gas_limit", None),
                max_fee=getattr(cfg, "default_max_fee", None),
                nonce=None,
                wait_for_inclusion=bool(wait_receipt),
            )
            tx_hash = submit.tx_hash
            LOG.info(
                "TX submitted ok=%s hash=%s endpoint=%s status=%s in %dms",
                submit.ok,
                submit.tx_hash,
                submit.endpoint,
                submit.status,
                submit.elapsed_ms,
            )
            if receipt:
                LOG.info(
                    "TX receipt: success=%s height=%s gas=%s",
                    receipt.success,
                    receipt.block_height,
                    receipt.gas_used,
                )

    return (commitment_hex, receipt if 'receipt' in locals() else None)


def run_loop(cfg: PosterEnv) -> None:
    """
    Long-running loop based on cfg.loop_interval_sec.
    Includes SIGINT/SIGTERM handling and simple jitterless cadence.
    """
    interval = float(getattr(cfg, "loop_interval_sec", 15.0))
    if interval <= 0:
        interval = 15.0

    # graceful shutdown flag
    stopping = {"flag": False}

    def _stop(signum, frame):  # noqa: ARG001
        LOG.warning("Received signal %s; stopping...", signum)
        stopping["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    LOG.info("Starting loop: every %.3fs (feed=%s, oracle=%s)", interval, cfg.feed_name, cfg.oracle_contract)
    while not stopping["flag"]:
        t0 = time.time()
        try:
            run_once(cfg)
        except Exception as e:  # pragma: no cover - daemon robustness
            LOG.exception("Iteration error: %s", e)
        # maintain cadence
        elapsed = time.time() - t0
        sleep_for = max(0.0, interval - elapsed)
        time.sleep(sleep_for)

    LOG.info("Stopped.")


# ======================================================================================
# CLI
# ======================================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Oracle DA Poster")
    p.add_argument("--once", action="store_true", help="Run exactly one iteration, then exit")
    p.add_argument("--dry-run", action="store_true", help="Do everything except submitting a transaction")
    p.add_argument("--no-da", dest="skip_da", action="store_true", help="Skip DA posting (use feed/meta commitment or local compute)")
    p.add_argument("--no-tx", dest="skip_tx", action="store_true", help="Skip transaction submission")
    p.add_argument("--print-args", action="store_true", help="Print the method/args/meta JSON before submitting")
    p.add_argument("--feed", type=str, default=None, help="Override feed name (defaults to $FEED_NAME or config)")
    p.add_argument("--wait-receipt", dest="wait_receipt", action="store_true", help="Wait for receipt after submit")
    p.add_argument("--no-wait-receipt", dest="wait_receipt", action="store_false", help="Do not wait for receipt")
    p.set_defaults(wait_receipt=None)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = build_arg_parser()
    args = ap.parse_args(argv)

    # Load environment/config
    cfg = load_env()
    LOG.info(
        "Config loaded: chain_id=%s tx_mode=%s oracle=%s feed=%s services_url=%s rpc_url=%s",
        getattr(cfg, "chain_id", None),
        getattr(cfg, "tx_mode", None),
        getattr(cfg, "oracle_contract", None),
        getattr(cfg, "feed_name", None),
        getattr(cfg, "services_url", None),
        getattr(cfg, "rpc_url", None),
    )

    # One-shot vs loop
    if args.once:
        try:
            commitment, receipt = run_once(
                cfg,
                feed_name=args.feed or None,
                dry_run=bool(args.dry_run),
                skip_da=bool(args.skip_da),
                skip_tx=bool(args.skip_tx),
                print_args=bool(args.print_args),
                wait_receipt=args.wait_receipt,
            )
            # Provide a compact machine-readable summary on stdout for piping
            summary = {
                "ok": True,
                "commitment": commitment,
                "receipt": (asdict(receipt) if receipt else None),
            }
            _print_json(summary)
            return 0
        except Exception as e:
            LOG.exception("Fatal error in one-shot run: %s", e)
            _print_json({"ok": False, "error": str(e)})
            return 1

    # Loop forever
    try:
        run_loop(cfg)
        return 0
    except Exception as e:  # pragma: no cover - top-level safety
        LOG.exception("Fatal error in loop: %s", e)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
