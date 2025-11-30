# -*- coding: utf-8 -*-
"""
verify.py
=========

Verify local contract sources against the *on-chain* code hash using
**studio-services**.

This tool speaks to the Studio Services verification endpoints:

- POST /verify
    Submit a verification job with {address|txHash|codeHash} + manifest + source.
- GET  /verify/{address}
- GET  /verify/{txHash}
    Query verification status/result.

It supports three common flows:

1) Address-based (most common)
   You already deployed and know the address. Submit sources; service recomputes
   the code hash for your manifest+source and matches it to the on-chain code hash
   for that address.

2) Tx-hash-based
   You deployed (or are about to) and want to bind verification to a specific tx.
   The service can resolve the created address / code hash from the tx receipt.

3) Code-hash-based (advanced)
   You know the exact code hash (e.g., from a block explorer artifact view).
   The service matches your rebuilt hash to that value.

The script can also compute a *local* code hash ( --dry-run / --local-hash ) using
the vm_py toolchain, so you can sanity-check before submitting to the service.

Environment (.env)
------------------
SERVICE_URL / SERVICES_URL  -> base URL for studio-services (e.g. http://127.0.0.1:8787)
RPC_URL                     -> optional, only for local-hash sanity or resolving chain data
CHAIN_ID                    -> optional; some services validate it
SERVICE_API_KEY             -> optional API key; sent as Authorization: Bearer <key>
STUDIO_API_KEY              -> alternate env var for API key (accepted here)

Examples
--------
# 1) Verify by address (manifest + source path):
python -m contracts.tools.verify \
  --address anim1qq... \
  --manifest contracts/build/counter/manifest.json \
  --source contracts/examples/counter/contract.py

# 2) Verify by tx hash (service resolves created address):
python -m contracts.tools.verify \
  --tx-hash 0x12ab... \
  --manifest contracts/build/counter/manifest.json \
  --source contracts/examples/counter/contract.py

# 3) Verify by explicit code hash:
python -m contracts.tools.verify \
  --code-hash 0xdeadbeef... \
  --manifest contracts/build/counter/manifest.json \
  --source contracts/examples/counter/contract.py

# 4) Dry-run: compute local code hash only (no network I/O):
python -m contracts.tools.verify \
  --manifest contracts/build/counter/manifest.json \
  --source contracts/examples/counter/contract.py \
  --local-hash

# 5) Submit and wait for completion (polling):
python -m contracts.tools.verify \
  --address anim1qq... \
  --manifest contracts/build/counter/manifest.json \
  --source contracts/examples/counter/contract.py \
  --wait --timeout 120

# 6) Only check status for an address already submitted:
python -m contracts.tools.verify --address anim1qq... --status

Notes
-----
- Input files (manifest/source) are *read and embedded* in the JSON request.
- If you also pass --abi, it is forwarded to the service (helpful for explorer UX).
- The exact verification semantics are implemented server-side per the repo's
  studio-services/models/verify.py and routers/verify.py expectations.

"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Shared helpers if available
try:
    from contracts.tools import canonical_json_str
    from contracts.tools import \
        find_project_root as _maybe_find_project_root  # type: ignore
    from contracts.tools import project_root as _project_root
except Exception:

    def canonical_json_str(obj: Any) -> str:
        return json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    def _project_root() -> Path:
        return Path(__file__).resolve().parents[2]

    def _maybe_find_project_root() -> Path:
        return _project_root()


# ------------------------------ .env loader ------------------------------- #


def _load_env() -> None:
    """
    Load a simple .env. Priority:
      $ENV_FILE > <repo>/contracts/.env > <repo>/.env
    """
    candidates = []
    env_file = os.environ.get("ENV_FILE")
    if env_file:
        candidates.append(Path(env_file))
    root = _maybe_find_project_root() if _maybe_find_project_root else _project_root()
    candidates += [root / "contracts" / ".env", root / ".env"]

    for p in candidates:
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    if s.lower().startswith("export "):
                        s = s[7:].strip()
                    if "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k, v)
                break
            except Exception:
                pass


# ------------------------------ HTTP client -------------------------------- #


class _HttpError(RuntimeError):
    pass


class _Http:
    def __init__(
        self, base_url: str, timeout: float = 15.0, api_key: Optional[str] = None
    ):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.api_key = api_key
        try:
            import requests  # type: ignore
        except Exception as exc:
            raise _HttpError("requests is required (pip install requests)") from exc
        self._requests = requests

    def _headers(self) -> Dict[str, str]:
        h = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_key:
            # Prefer Authorization: Bearer but also set X-API-Key for leniency
            h["Authorization"] = f"Bearer {self.api_key}"
            h["X-API-Key"] = self.api_key
        return h

    def post(self, path: str, json_body: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base}{path}"
        r = self._requests.post(
            url, headers=self._headers(), json=json_body, timeout=self.timeout
        )
        if not r.ok:
            try:
                payload = r.json()
            except Exception:
                payload = {"error": r.text}
            raise _HttpError(f"POST {path} -> {r.status_code}: {payload}")
        try:
            return r.json()
        except Exception as exc:
            raise _HttpError(f"POST {path} invalid JSON: {exc}") from exc

    def get(self, path: str) -> Dict[str, Any]:
        url = f"{self.base}{path}"
        r = self._requests.get(url, headers=self._headers(), timeout=self.timeout)
        if not r.ok:
            try:
                payload = r.json()
            except Exception:
                payload = {"error": r.text}
            raise _HttpError(f"GET {path} -> {r.status_code}: {payload}")
        try:
            return r.json()
        except Exception as exc:
            raise _HttpError(f"GET {path} invalid JSON: {exc}") from exc


# ------------------------------ IO helpers --------------------------------- #


def _read_json_file(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to parse JSON: {p}: {exc}") from exc


def _read_text_file(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception as exc:
        raise ValueError(f"Failed to read text: {p}: {exc}") from exc


def _as_hex(b: bytes) -> str:
    return "0x" + binascii.hexlify(b).decode()


# ----------------------- Optional local hash (vm_py) ----------------------- #


@dataclass
class LocalHashResult:
    ok: bool
    code_hash: Optional[str]
    ir_bytes_len: Optional[int]
    details: str


def _sha3_256(data: bytes) -> str:
    try:
        import hashlib

        h = hashlib.sha3_256()
        h.update(data)
        return _as_hex(h.digest())
    except Exception:
        # Fallback to pysha3 for older envs (not needed in 3.11+)
        try:
            import sha3  # type: ignore

            h = sha3.sha3_256()
            h.update(data)
            return _as_hex(h.digest())
        except Exception as exc:
            raise RuntimeError(f"sha3_256 unavailable: {exc}") from exc


def _compute_local_code_hash(manifest_path: Path, source_path: Path) -> LocalHashResult:
    """
    Best-effort local hash computation (does not contact services).
    Strategy:
      - Prefer vm_py.runtime.loader to compile+link → IR bytes
      - If unavailable, try a minimal encode path under vm_py.compiler.encode
      - Hash the final code bytes using sha3_256 (matching service conventions)
    """
    try:
        manifest = _read_json_file(manifest_path)
        source = _read_text_file(source_path)
    except Exception as exc:
        return LocalHashResult(False, None, None, f"read inputs failed: {exc}")

    # Try vm_py toolchain
    try:
        from vm_py.runtime.loader import \
            load_package as _load_pkg  # type: ignore

        # Some versions expose compile & pack differently; accept both dict/bytes returns.
        pkg = {"manifest": manifest, "source": source}
        compiled = _load_pkg(pkg)  # may raise
        # Accept either dict with "code" or a tuple (ir, meta)
        if isinstance(compiled, dict) and "code" in compiled:
            code_bytes = compiled["code"]
            if isinstance(code_bytes, str):
                # Might already be hex or base64; assume hex w/o 0x
                try:
                    code_bytes = binascii.unhexlify(
                        code_bytes[2:] if code_bytes.startswith("0x") else code_bytes
                    )
                except Exception:
                    code_bytes = code_bytes.encode("utf-8")
        elif isinstance(compiled, (tuple, list)) and len(compiled) >= 1:
            code_bytes = compiled[0]
        else:
            # Last resort: serialize whole 'compiled' object deterministically
            code_bytes = json.dumps(
                compiled, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")

        if not isinstance(code_bytes, (bytes, bytearray)):
            code_bytes = bytes(code_bytes)
        code_hash = _sha3_256(code_bytes)
        return LocalHashResult(
            True, code_hash, len(code_bytes), "vm_py.runtime.loader path"
        )
    except Exception:
        pass

    # Fallback path — attempt vm_py.compiler.encode directly
    try:
        from vm_py.compiler import encode as _enc  # type: ignore
        from vm_py.runtime import loader as _ldr  # type: ignore

        # Some versions allow: _ldr.compile_source(manifest, source) -> ir
        if hasattr(_ldr, "compile_source"):
            ir = _ldr.compile_source(manifest=manifest, source=source)
        else:
            # Very conservative: pack object and hope encode knows what to do.
            ir = {"manifest": manifest, "source": source}
        # Encode to canonical bytes
        encode_fun = None
        for name in ("to_bytes", "encode", "ir_to_bytes"):
            if hasattr(_enc, name):
                encode_fun = getattr(_enc, name)
                break
        if not encode_fun:
            raise RuntimeError("vm_py.compiler.encode lacks a byte encoder")
        ir_bytes = encode_fun(ir)
        if not isinstance(ir_bytes, (bytes, bytearray)):
            ir_bytes = bytes(ir_bytes)
        code_hash = _sha3_256(ir_bytes)
        return LocalHashResult(
            True, code_hash, len(ir_bytes), "vm_py.compiler.encode path"
        )
    except Exception as exc:
        return LocalHashResult(
            False, None, None, f"vm_py not available or failed: {exc}"
        )


# ----------------------------- Payload builders ---------------------------- #


def _normalize_code_hash(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    t = s.lower().strip()
    if t.startswith("0x"):
        return t
    # best guess hex
    if all(c in "0123456789abcdef" for c in t):
        return "0x" + t
    return t  # leave as-is


def _build_submit_payload(
    address: Optional[str],
    tx_hash: Optional[str],
    code_hash: Optional[str],
    manifest: Dict[str, Any],
    source_text: str,
    abi: Optional[Dict[str, Any]],
    chain_id: Optional[int],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "manifest": manifest,
        "source": source_text,
    }
    if abi is not None:
        payload["abi"] = abi
    if chain_id is not None:
        payload["chainId"] = chain_id
    if address:
        payload["address"] = address
    if tx_hash:
        payload["txHash"] = tx_hash
    if code_hash:
        payload["codeHash"] = _normalize_code_hash(code_hash)
    return payload


# --------------------------------- CLI ------------------------------------- #


def _parse_cli(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="contracts.tools.verify",
        description="Verify local sources ↔ on-chain code hash via studio-services.",
    )

    # Target identifiers (one of these is required when submitting; optional for --status)
    p.add_argument(
        "--address",
        type=str,
        default=None,
        help="Deployed contract address (bech32m anim1… or hex)",
    )
    p.add_argument(
        "--tx-hash", type=str, default=None, help="Deployment transaction hash (0x...)"
    )
    p.add_argument(
        "--code-hash", type=str, default=None, help="Explicit code hash (0x...)"
    )

    # Inputs
    p.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Path to manifest.json (required unless --status only)",
    )
    p.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Path to contract source (required unless --status only)",
    )
    p.add_argument(
        "--abi", type=Path, default=None, help="Optional ABI JSON; forwarded to service"
    )

    # Service
    p.add_argument(
        "--service-url",
        type=str,
        default=None,
        help="Base URL for studio-services (env: SERVICE_URL / SERVICES_URL)",
    )
    p.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key (env: SERVICE_API_KEY / STUDIO_API_KEY)",
    )
    p.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout seconds")

    # Behavior
    p.add_argument(
        "--status",
        action="store_true",
        help="Do not submit; only query status for {address|tx}",
    )
    p.add_argument(
        "--wait",
        action="store_true",
        help="After submit, poll until result (success/failure)",
    )
    p.add_argument(
        "--poll-interval", type=float, default=1.0, help="Polling interval seconds"
    )
    p.add_argument(
        "--wait-timeout",
        type=float,
        default=120.0,
        help="Max seconds to wait when --wait",
    )

    # Local-only sanity
    p.add_argument(
        "--local-hash",
        "--dry-run",
        dest="local_hash",
        action="store_true",
        help="Compute and print local code hash only; do not contact service",
    )

    # Misc
    p.add_argument(
        "--chain-id",
        type=int,
        default=None,
        help="Optional chainId hint forwarded to service",
    )
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    return p.parse_args(argv)


# ------------------------------ Status helpers ----------------------------- #


def _status_path(address: Optional[str], tx_hash: Optional[str]) -> str:
    if address:
        return f"/verify/{address}"
    if tx_hash:
        return f"/verify/{tx_hash}"
    raise ValueError("Status requires --address or --tx-hash")


def _maybe_print_json(obj: Any, as_json: bool) -> None:
    if as_json:
        print(canonical_json_str(obj))
    else:
        print(json.dumps(obj, indent=2, ensure_ascii=False))


# ---------------------------------- main ----------------------------------- #


def main(argv=None) -> int:
    _load_env()
    args = _parse_cli(argv)

    # Resolve service URL and API key
    service_url = (
        args.service_url
        or os.environ.get("SERVICE_URL")
        or os.environ.get("SERVICES_URL")
        or os.environ.get("STUDIO_SERVICES_URL")
        or "http://127.0.0.1:8787"
    )
    api_key = (
        args.api_key
        or os.environ.get("SERVICE_API_KEY")
        or os.environ.get("STUDIO_API_KEY")
        or os.environ.get("API_KEY")
    )

    # 1) Local hash path (no network)
    if args.local_hash:
        if not args.manifest or not args.source:
            print(
                "[verify] ERROR: --local-hash requires --manifest and --source",
                file=sys.stderr,
            )
            return 2
        res = _compute_local_code_hash(args.manifest, args.source)
        out = {
            "ok": res.ok,
            "codeHash": res.code_hash,
            "irBytes": res.ir_bytes_len,
            "details": res.details,
            "manifest": str(args.manifest),
            "source": str(args.source),
        }
        _maybe_print_json(out, args.json)
        return 0 if res.ok else 3

    # 2) Status-only path
    if args.status:
        try:
            http = _Http(service_url, timeout=args.timeout, api_key=api_key)
        except Exception as exc:
            print(f"[verify] ERROR: init HTTP failed: {exc}", file=sys.stderr)
            return 2
        if not args.address and not args.tx_hash:
            print(
                "[verify] ERROR: --status requires --address or --tx-hash",
                file=sys.stderr,
            )
            return 2
        try:
            status = http.get(_status_path(args.address, args.tx_hash))
        except Exception as exc:
            print(f"[verify] ERROR: status fetch failed: {exc}", file=sys.stderr)
            return 3
        _maybe_print_json(status, args.json)
        # Consider "verified": True as success, otherwise exit 4 to be explicit
        if isinstance(status, dict) and status.get("verified") is True:
            return 0
        return 4

    # 3) Submit verification job
    if not args.manifest or not args.source:
        print(
            "[verify] ERROR: submitting requires --manifest and --source",
            file=sys.stderr,
        )
        return 2
    if not (args.address or args.tx_hash or args.code_hash):
        print(
            "[verify] ERROR: one of --address, --tx-hash, --code-hash must be provided",
            file=sys.stderr,
        )
        return 2

    # Read inputs
    try:
        manifest = _read_json_file(args.manifest)
        source_text = _read_text_file(args.source)
        abi = _read_json_file(args.abi) if args.abi else None
    except Exception as exc:
        print(f"[verify] ERROR: input read failed: {exc}", file=sys.stderr)
        return 2

    # Build payload and POST
    try:
        http = _Http(service_url, timeout=args.timeout, api_key=api_key)
    except Exception as exc:
        print(f"[verify] ERROR: init HTTP failed: {exc}", file=sys.stderr)
        return 2

    payload = _build_submit_payload(
        address=args.address,
        tx_hash=args.tx_hash,
        code_hash=args.code_hash,
        manifest=manifest,
        source_text=source_text,
        abi=abi,
        chain_id=args.chain_id,
    )

    try:
        submit_res = http.post("/verify", payload)
    except Exception as exc:
        print(f"[verify] ERROR: submit failed: {exc}", file=sys.stderr)
        return 3

    if not args.wait:
        # Print immediate response (usually contains job id or accepted=true)
        _maybe_print_json(submit_res, args.json)
        return 0

    # 4) Poll until done
    # Prefer to poll by address if available, else by tx-hash. If neither, try code-hash
    # (but services might not provide a status path for raw code-hash).
    poll_path = None
    if args.address:
        poll_path = _status_path(args.address, None)
    elif args.tx_hash:
        poll_path = _status_path(None, args.tx_hash)
    else:
        # As a last resort, if service returns something like {"address": "..."} use that.
        addr = None
        if isinstance(submit_res, dict):
            addr = submit_res.get("address") or submit_res.get("resolvedAddress")
        if addr:
            poll_path = _status_path(addr, None)

    if not poll_path:
        _maybe_print_json(
            {
                "ok": True,
                "submitted": True,
                "note": "Service accepted job but no polling path is available; skipping wait.",
                "response": submit_res,
            },
            args.json,
        )
        return 0

    deadline = time.time() + float(args.wait_timeout)
    last_status: Optional[Dict[str, Any]] = None
    while time.time() < deadline:
        try:
            last_status = http.get(poll_path)
        except Exception as exc:
            # Keep polling on transient errors
            last_status = {"error": str(exc)}
        if isinstance(last_status, dict):
            if last_status.get("status") in ("verified", "failed", "error", "mismatch"):
                break
            if last_status.get("verified") is True:
                # Some services may directly include the terminal flag
                break
        time.sleep(float(args.poll_interval))

    out = {
        "ok": bool(
            isinstance(last_status, dict) and last_status.get("verified") is True
        ),
        "submitted": True,
        "result": last_status,
        "submittedResponse": submit_res,
    }
    _maybe_print_json(out, args.json)

    return 0 if out["ok"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
