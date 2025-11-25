# -*- coding: utf-8 -*-
"""
deploy.py
=========

Deploy a compiled contract package using the Python SDK (omni_sdk).

What this does
--------------
- Loads RPC URL / chainId from CLI or environment (.env supported).
- Loads a *package directory* that contains a manifest.json (and code blob).
- Derives a signer from a mnemonic (Dilithium3 by default; SPHINCS+ optional).
- Uses omni_sdk high-level deploy helpers when available; otherwise falls back
  to a best-effort thin wrapper around the SDK building/tx-send primitives.
- Submits the deploy transaction via JSON-RPC, waits for a receipt, and writes
  a deployments registry file under contracts/build/deployments/<network>.json.

Inputs/assumptions
------------------
Package layout (produced by contracts/tools/build_package.py):

  <pkg_dir>/
    manifest.json         # required, includes ABI and code hash
    code.ir               # optional (or code.bin); referenced by manifest["code"]["path"]

The manifest must conform to contracts/schemas/manifest.schema.json (or spec).

Environment (optional; .env in repo root or contracts/):
  RPC_URL           = http://127.0.0.1:8545
  CHAIN_ID          = 1337
  DEPLOYER_MNEMONIC = <twelve-plus-words>
  PQ_ALG            = dilithium3     # or sphincs_shake_128s

CLI
---
python -m contracts.tools.deploy --package contracts/build/counter-pkg \
  --rpc $RPC_URL --chain-id 1337 \
  --mnemonic "$DEPLOYER_MNEMONIC" --alg dilithium3 --wait --timeout 120

Or rely on .env:
python -m contracts.tools.deploy --package contracts/build/counter-pkg --wait

Outputs
-------
- Prints JSON to stdout with txHash, contractAddress, gasUsed, blockNumber.
- Writes/update contracts/build/deployments/<chainId>.json registry.

Exit codes: 0 on success; non-zero on error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Local helpers shared by tools (added in contracts/tools/__init__.py)
try:
    from contracts.tools import (  # type: ignore
        atomic_write_text,
        canonical_json_str,
        ensure_dir,
        find_project_root as _maybe_find_project_root,  # optional alias
        project_root as _project_root,
    )
except Exception:
    # Minimal fallbacks in case tools/__init__.py isn't available at runtime
    def canonical_json_str(obj: Any) -> str:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def atomic_write_text(path: Path, data: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, path)

    def ensure_dir(p: Path) -> None:
        p.mkdir(parents=True, exist_ok=True)

    def _project_root() -> Path:
        return Path(__file__).resolve().parents[2]

    def _maybe_find_project_root() -> Path:
        return _project_root()


# ---------------------------------------------------------------------------
# Lightweight .env loader (no external dependency)
# ---------------------------------------------------------------------------

def _load_env_dotenv() -> None:
    """
    Load first .env file found in:
      - $ENV_FILE if set
      - <repo>/contracts/.env
      - <repo>/.env
    Supports simple KEY=VALUE lines; ignores comments and exports.
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
                    v = v.strip().strip("'").strip('"')
                    os.environ.setdefault(k, v)
                break
            except Exception:
                # Soft-fail on malformed .env
                pass


# ---------------------------------------------------------------------------
# omni_sdk adapters (resilient to small API differences)
# ---------------------------------------------------------------------------

class _SdkError(RuntimeError):
    pass


class _Rpc:
    """
    Minimal wrapper over omni_sdk.rpc.http or a tiny built-in JSON-RPC client.
    """
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url
        self.timeout = timeout
        self._session = None

        # Try omni_sdk if present, else fallback to requests
        self._client = None
        try:
            from omni_sdk.rpc.http import HttpClient as _HttpClient  # type: ignore
            self._client = _HttpClient(url, timeout=timeout)
        except Exception:
            try:
                import requests  # type: ignore

                class _ReqWrapper:
                    def __init__(self, url: str, timeout: float):
                        self.url = url
                        self.timeout = timeout
                        self._id = 0

                    def call(self, method: str, params: Any = None) -> Any:
                        self._id += 1
                        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
                        r = requests.post(self.url, json=payload, timeout=self.timeout)
                        r.raise_for_status()
                        data = r.json()
                        if "error" in data and data["error"]:
                            raise _SdkError(f"RPC error {data['error'].get('code')}: {data['error'].get('message')}")
                        return data["result"]

                self._client = _ReqWrapper(url, timeout)
            except Exception as exc:
                raise _SdkError(
                    f"Unable to initialize RPC client for {url} "
                    f"(install omni-sdk or requests): {exc}"
                ) from exc

    def call(self, method: str, params: Any = None) -> Any:
        return self._client.call(method, params or [])


class _Signer:
    """
    Thin loader for PQ signers from omni_sdk.wallet.signer. Supports:
      - Dilithium3 (default)
      - SPHINCS+ SHAKE-128s
    """
    def __init__(self, algo: str, account_index: int = 0):
        self.algo = (algo or "dilithium3").lower()
        self.account_index = account_index
        self._signer = None

    @staticmethod
    def _from_mnemonic_via_sdk(mnemonic: str, algo: str, account_index: int):
        # Try generic Signer class first
        try:
            from omni_sdk.wallet.signer import Signer  # type: ignore
            if hasattr(Signer, "from_mnemonic"):
                return Signer.from_mnemonic(mnemonic, alg=algo, account_index=account_index)
        except Exception:
            pass

        # Specific classes
        try:
            if algo.startswith("dilithium"):
                from omni_sdk.wallet.signer import Dilithium3Signer  # type: ignore
                return Dilithium3Signer.from_mnemonic(mnemonic, account_index=account_index)
            if algo.startswith("sphincs"):
                from omni_sdk.wallet.signer import SphincsShake128sSigner  # type: ignore
                return SphincsShake128sSigner.from_mnemonic(mnemonic, account_index=account_index)
        except Exception as exc:
            raise _SdkError(f"Could not initialize signer ({algo}): {exc}") from exc

        raise _SdkError("No compatible signer in omni_sdk.wallet.signer")

    @classmethod
    def from_mnemonic(cls, mnemonic: str, algo: str = "dilithium3", account_index: int = 0) -> "_Signer":
        s = cls(algo=algo, account_index=account_index)
        s._signer = cls._from_mnemonic_via_sdk(mnemonic, algo, account_index)
        return s

    # Facade
    @property
    def address(self) -> str:
        # all signers should expose bech32m or hex address; try common names
        for attr in ("address", "addr", "bech32", "bech32m"):
            if hasattr(self._signer, attr):
                v = getattr(self._signer, attr)
                return v() if callable(v) else v
        # derive from pubkey if needed
        try:
            from omni_sdk.address import address_from_pubkey  # type: ignore
            pub = self.public_key_bytes()
            return address_from_pubkey(self.algo, pub)
        except Exception:
            raise _SdkError("Signer does not expose an address property and address_from_pubkey is not available")

    def public_key_bytes(self) -> bytes:
        for attr in ("public_key", "pubkey", "pub_key"):
            if hasattr(self._signer, attr):
                v = getattr(self._signer, attr)
                return v if isinstance(v, (bytes, bytearray)) else (v() if callable(v) else bytes(v))
        if hasattr(self._signer, "export_public_key"):
            return self._signer.export_public_key()
        raise _SdkError("Cannot obtain public key bytes from signer")

    # Pass-through sign for SDK tx builder to consume
    def sign(self, sign_bytes: bytes) -> Dict[str, Any]:
        """
        Returns a dict { 'alg_id': str/int, 'pubkey': bytes, 'signature': bytes }
        using omni_sdk's signer surface when possible.
        """
        s = self._signer
        # Preferred: s.sign(sign_bytes) → returns structured dict
        if hasattr(s, "sign"):
            res = s.sign(sign_bytes)
            if isinstance(res, dict):
                return res
            # fallthrough: some signers return (sig, pubkey)
            if isinstance(res, (tuple, list)) and len(res) == 2:
                sig, pub = res
                return {"alg_id": getattr(s, "alg_id", self.algo), "pubkey": pub, "signature": sig}
        # Known method names
        for meth in ("sign_detached", "sign_message"):
            if hasattr(s, meth):
                sig = getattr(s, meth)(sign_bytes)
                pub = self.public_key_bytes()
                return {"alg_id": getattr(s, "alg_id", self.algo), "pubkey": pub, "signature": sig}
        raise _SdkError("Signer lacks a compatible sign(sign_bytes) method")


# ---------------------------------------------------------------------------
# Package I/O
# ---------------------------------------------------------------------------

def _load_manifest(pkg_dir: Path) -> Dict[str, Any]:
    mpath = pkg_dir / "manifest.json"
    if not mpath.is_file():
        raise FileNotFoundError(f"manifest.json not found in {pkg_dir}")
    try:
        return json.loads(mpath.read_text(encoding="utf-8"))
    except Exception as exc:
        raise _SdkError(f"Failed to parse manifest.json: {exc}") from exc


def _resolve_code_blob(pkg_dir: Path, manifest: Dict[str, Any]) -> Tuple[Optional[Path], Optional[bytes]]:
    # Expect manifest["code"] to describe the code object
    code = manifest.get("code") or {}
    path_hint = code.get("path") or code.get("file") or code.get("blob")
    if path_hint:
        p = pkg_dir / str(path_hint)
        if not p.is_file():
            raise FileNotFoundError(f"Code file referenced by manifest not found: {p}")
        return p, p.read_bytes()
    # Some manifests embed the code bytes under "bytes" (hex/base64 not handled here)
    if "bytes" in code and isinstance(code["bytes"], str):
        try:
            import binascii
            # Try hex first; if fails, try base64
            try:
                return None, binascii.unhexlify(code["bytes"].removeprefix("0x"))
            except Exception:
                import base64
                return None, base64.b64decode(code["bytes"])
        except Exception as exc:
            raise _SdkError(f"Unable to decode embedded code bytes: {exc}") from exc
    return None, None


# ---------------------------------------------------------------------------
# High-level deploy via omni_sdk.contracts.deployer (if available)
# ---------------------------------------------------------------------------

def _sdk_deploy_via_deployer(
    rpc: _Rpc,
    chain_id: int,
    signer: _Signer,
    pkg_dir: Path,
    gas_price: Optional[int],
    gas_limit: Optional[int],
    wait: bool,
    timeout: float,
) -> Dict[str, Any]:
    try:
        from omni_sdk.contracts.deployer import deploy_package, Deployer  # type: ignore
    except Exception:
        # Fallback to generic path
        return _sdk_deploy_fallback(rpc, chain_id, signer, pkg_dir, gas_price, gas_limit, wait, timeout)

    manifest = _load_manifest(pkg_dir)
    code_path, code_bytes = _resolve_code_blob(pkg_dir, manifest)

    # Prefer high-level function if present
    try:
        if "deploy_package" in globals() or "deploy_package" in locals():
            # Common signature variants:
            # deploy_package(rpc_url, chain_id, pkg_dir, signer=..., gas_price=..., gas_limit=..., wait=True, timeout=...)
            return deploy_package(  # type: ignore
                rpc.url, chain_id, str(pkg_dir),
                signer=signer._signer, gas_price=gas_price, gas_limit=gas_limit,
                wait=wait, timeout=timeout,
            )
    except TypeError:
        pass
    except Exception as exc:
        raise _SdkError(f"deploy_package failed: {exc}") from exc

    # Try Deployer class API (guessy but resilient)
    try:
        d = Deployer(rpc_url=rpc.url, chain_id=chain_id, signer=signer._signer)  # type: ignore
        if hasattr(d, "deploy_dir"):
            # deploy_dir(pkg_dir, gas_price=?, gas_limit=?, wait=?, timeout=?)
            return d.deploy_dir(str(pkg_dir), gas_price=gas_price, gas_limit=gas_limit, wait=wait, timeout=timeout)
        if hasattr(d, "deploy"):
            return d.deploy(manifest=manifest, code_path=str(code_path) if code_path else None,
                            code_bytes=code_bytes, gas_price=gas_price, gas_limit=gas_limit,
                            wait=wait, timeout=timeout)
    except Exception as exc:
        raise _SdkError(f"Deployer API failed: {exc}") from exc

    # Fall back
    return _sdk_deploy_fallback(rpc, chain_id, signer, pkg_dir, gas_price, gas_limit, wait, timeout)


# ---------------------------------------------------------------------------
# Fallback builder path (SDK primitives: build → encode → sign → send)
# ---------------------------------------------------------------------------

def _sdk_deploy_fallback(
    rpc: _Rpc,
    chain_id: int,
    signer: _Signer,
    pkg_dir: Path,
    gas_price: Optional[int],
    gas_limit: Optional[int],
    wait: bool,
    timeout: float,
) -> Dict[str, Any]:
    """
    Compose a deploy tx using omni_sdk.tx.* primitives with broad compatibility.
    """
    manifest = _load_manifest(pkg_dir)
    code_path, code_bytes = _resolve_code_blob(pkg_dir, manifest)

    # 1) Fetch nonce for sender
    sender = signer.address
    try:
        nonce = int(rpc.call("state.getNonce", [sender]))
    except Exception as exc:
        raise _SdkError(f"Failed to fetch nonce for {sender}: {exc}") from exc

    # 2) Build deploy tx
    try:
        # Prefer omni_sdk.tx.build API
        from omni_sdk.tx import build as tx_build  # type: ignore
        # Common variants:
        # build.deploy(manifest, code_bytes|code_path, sender, nonce, chain_id, gas_price=?, gas_limit=?)
        build_fn = None
        for name in ("deploy", "build_deploy", "make_deploy"):
            if hasattr(tx_build, name):
                build_fn = getattr(tx_build, name)
                break
        if build_fn is None:
            raise _SdkError("omni_sdk.tx.build has no deploy builder; update SDK")
        tx_obj = None
        try:
            if code_bytes is not None:
                tx_obj = build_fn(manifest=manifest, code_bytes=code_bytes,
                                  sender=sender, nonce=nonce, chain_id=chain_id,
                                  gas_price=gas_price, gas_limit=gas_limit)
            else:
                tx_obj = build_fn(manifest=manifest, code_path=str(code_path) if code_path else None,
                                  sender=sender, nonce=nonce, chain_id=chain_id,
                                  gas_price=gas_price, gas_limit=gas_limit)
        except TypeError:
            # Try positional signature fallback
            if code_bytes is not None:
                tx_obj = build_fn(manifest, code_bytes, sender, nonce, chain_id, gas_price, gas_limit)
            else:
                tx_obj = build_fn(manifest, str(code_path) if code_path else None, sender, nonce, chain_id, gas_price, gas_limit)
    except Exception as exc:
        raise _SdkError(f"Failed to build deploy tx: {exc}") from exc

    # 3) Encode SignBytes and sign
    try:
        from omni_sdk.tx import encode as tx_encode  # type: ignore
        get_sign_bytes = None
        for name in ("sign_bytes", "get_sign_bytes", "encode_sign_bytes"):
            if hasattr(tx_encode, name):
                get_sign_bytes = getattr(tx_encode, name)
                break
        if get_sign_bytes is None:
            raise _SdkError("omni_sdk.tx.encode missing sign-bytes helper")
        sign_bytes = get_sign_bytes(tx_obj, chain_id=chain_id)
        sig = signer.sign(sign_bytes)
        # Attach signature
        if hasattr(tx_encode, "attach_signature"):
            signed = tx_encode.attach_signature(tx_obj, sig)  # type: ignore
        else:
            # Fallback: attempt attribute or dict merge
            if isinstance(tx_obj, dict):
                tx_obj["signature"] = sig
                signed = tx_obj
            else:
                # try dataclass-like
                setattr(tx_obj, "signature", sig)
                signed = tx_obj
        # Encode to CBOR/bytes for tx.sendRawTransaction
        tx_bytes = None
        for name in ("to_bytes", "encode", "to_cbor"):
            if hasattr(tx_encode, name):
                try:
                    tx_bytes = getattr(tx_encode, name)(signed)  # type: ignore
                    break
                except TypeError:
                    # maybe needs explicit flag
                    try:
                        tx_bytes = getattr(tx_encode, name)(signed, canonical=True)  # type: ignore
                        break
                    except Exception:
                        pass
        if tx_bytes is None:
            raise _SdkError("Could not encode signed tx to bytes")
    except Exception as exc:
        raise _SdkError(f"Failed to sign/encode deploy tx: {exc}") from exc

    # 4) Send & await receipt
    try:
        # Most nodes expose tx.sendRawTransaction(hex|base64|bin). Prefer hex.
        import binascii
        raw_hex = "0x" + binascii.hexlify(tx_bytes).decode()
        tx_hash = rpc.call("tx.sendRawTransaction", [raw_hex])
    except Exception as exc:
        raise _SdkError(f"tx.sendRawTransaction failed: {exc}") from exc

    receipt = None
    contract_addr = None
    if wait:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                receipt = rpc.call("tx.getTransactionReceipt", [tx_hash])
                if receipt:
                    contract_addr = receipt.get("contractAddress") or receipt.get("contract_address")
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not receipt:
            raise _SdkError(f"Timed out waiting for receipt ({tx_hash})")

    # best-effort result shape
    result = {
        "txHash": tx_hash,
        "from": sender,
        "contractAddress": contract_addr,
        "receipt": receipt,
    }
    return result


# ---------------------------------------------------------------------------
# Registry writer
# ---------------------------------------------------------------------------

def _write_deploy_registry(chain_id: int, result: Dict[str, Any], manifest: Dict[str, Any]) -> Path:
    root = _maybe_find_project_root() if _maybe_find_project_root else _project_root()
    reg_dir = root / "contracts" / "build" / "deployments"
    ensure_dir(reg_dir)
    reg_path = reg_dir / f"{chain_id}.json"

    current: Dict[str, Any] = {}
    if reg_path.is_file():
        try:
            current = json.loads(reg_path.read_text(encoding="utf-8"))
        except Exception:
            current = {}

    addr = result.get("contractAddress") or ""
    ent = {
        "name": manifest.get("name"),
        "txHash": result.get("txHash"),
        "address": addr,
        "abi": manifest.get("abi"),
        "codeHash": (manifest.get("code") or {}).get("hash"),
        "timestamp": int(time.time()),
    }

    # keyed by codeHash or by name+timestamp if missing
    key = ent["codeHash"] or f"{manifest.get('name', 'contract')}-{ent['timestamp']}"
    current[key] = ent

    atomic_write_text(reg_path, canonical_json_str(current))
    return reg_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="contracts.tools.deploy",
        description="Deploy a compiled contract package via omni_sdk (Animica).",
    )
    p.add_argument("--package", type=Path, required=True, help="Path to package directory (contains manifest.json)")
    p.add_argument("--rpc", type=str, default=None, help="RPC URL (e.g., http://127.0.0.1:8545)")
    p.add_argument("--chain-id", type=int, default=None, help="Chain ID (e.g., 1337)")
    p.add_argument("--mnemonic", type=str, default=None, help="Deployer mnemonic (can also come from DEPLOYER_MNEMONIC)")
    p.add_argument("--alg", type=str, default=None, help="PQ alg: dilithium3 (default) or sphincs_shake_128s")
    p.add_argument("--account-index", type=int, default=0, help="HD account index (default: 0)")

    p.add_argument("--gas-price", type=int, default=None, help="Override gas price (wei-like units)")
    p.add_argument("--gas-limit", type=int, default=None, help="Override gas limit")

    p.add_argument("--wait", action="store_true", help="Wait for receipt and contract address")
    p.add_argument("--timeout", type=float, default=120.0, help="Wait timeout seconds (default: 120)")

    p.add_argument("--no-registry", action="store_true", help="Do not write deployments registry file")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON result to stdout")

    return p.parse_args(argv)


def main(argv=None) -> int:
    _load_env_dotenv()
    args = _parse_args(argv)

    pkg_dir = args.package.resolve()
    if not pkg_dir.is_dir():
        print(f"[deploy] ERROR: package directory not found: {pkg_dir}", file=sys.stderr)
        return 2

    rpc_url = args.rpc or os.environ.get("RPC_URL")
    if not rpc_url:
        print("[deploy] ERROR: RPC URL not provided (use --rpc or RPC_URL)", file=sys.stderr)
        return 2

    # Chain ID: CLI > ENV > RPC (chain.getChainId or chain.getParams)
    chain_id: Optional[int] = args.chain_id or (int(os.environ.get("CHAIN_ID", "0")) or None)
    rpc = _Rpc(rpc_url, timeout=15.0)

    if chain_id is None:
        try:
            chain_id = int(rpc.call("chain.getChainId", []))
        except Exception:
            try:
                params = rpc.call("chain.getParams", [])
                # Common nests: params["chainId"] or params["chain"]["id"]
                chain_id = int(params.get("chainId") or (params.get("chain") or {}).get("id"))
            except Exception as exc:
                print(f"[deploy] ERROR: could not determine chainId via RPC: {exc}", file=sys.stderr)
                return 2

    mnemonic = args.mnemonic or os.environ.get("DEPLOYER_MNEMONIC")
    if not mnemonic:
        print("[deploy] ERROR: mnemonic not provided (use --mnemonic or DEPLOYER_MNEMONIC)", file=sys.stderr)
        return 2

    alg = (args.alg or os.environ.get("PQ_ALG") or "dilithium3").lower()
    try:
        signer = _Signer.from_mnemonic(mnemonic, algo=alg, account_index=args.account_index)
    except Exception as exc:
        print(f"[deploy] ERROR: failed to init signer: {exc}", file=sys.stderr)
        return 2

    try:
        result = _sdk_deploy_via_deployer(
            rpc=rpc,
            chain_id=int(chain_id),
            signer=signer,
            pkg_dir=pkg_dir,
            gas_price=args.gas_price,
            gas_limit=args.gas_limit,
            wait=bool(args.wait),
            timeout=float(args.timeout),
        )
    except Exception as exc:
        print(f"[deploy] ERROR: deploy failed: {exc}", file=sys.stderr)
        return 3

    # Best effort derive manifest for registry
    manifest: Dict[str, Any] = {}
    try:
        manifest = _load_manifest(pkg_dir)
    except Exception:
        pass

    if not args.no_registry and manifest:
        try:
            reg_path = _write_deploy_registry(int(chain_id), result, manifest)
            print(f"[deploy] registry updated: {reg_path}", file=sys.stderr)
        except Exception as exc:
            print(f"[deploy] WARN: failed to update registry: {exc}", file=sys.stderr)

    # Human & JSON output
    txh = result.get("txHash")
    addr = result.get("contractAddress")
    if args.json:
        print(canonical_json_str({"txHash": txh, "contractAddress": addr, "result": result}))
    else:
        print(f"txHash: {txh}")
        if addr:
            print(f"contractAddress: {addr}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
