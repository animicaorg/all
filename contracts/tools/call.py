# -*- coding: utf-8 -*-
"""
call.py
=======

Small CLI helper to *call* a contract function for local testing.

Supports two modes:

1) Read-only (default): simulate a contract method without state changes.
   Tries omni_sdk high-level ContractClient or RPC simulate endpoints if present.

2) Send (--send): build a state-changing call transaction, sign it with a PQ
   signer (Dilithium3 or SPHINCS+), submit via RPC, and optionally wait
   for the receipt.

It will:
- Load RPC URL / chainId from CLI or .env (contracts/.env or repo .env)
- Load ABI from:
    * --manifest <path/to/manifest.json>  (preferred)
    * --abi <path/to/abi.json>
  (When using --send, you only *need* ABI; manifest is useful but optional.)
- Resolve function + args (JSON or key=value pairs).
- For --send, derive sender from mnemonic (or --from address) and sign.

Examples
--------
# Read-only call (simulate):
python -m contracts.tools.call \\
  --address anim1qq... \\
  --abi contracts/build/counter/abi.json \\
  --fn get

# State-changing call (send) then wait for receipt:
python -m contracts.tools.call \\
  --send --wait --timeout 90 \\
  --address anim1qq... \\
  --abi contracts/build/counter/abi.json \\
  --fn inc --args-json '{}' \\
  --mnemonic "$DEPLOYER_MNEMONIC"

# Positional args:
python -m contracts.tools.call --address anim1... --abi abi.json --fn setMany \\
  --pos '[1,2,3]'

Environment (.env)
------------------
RPC_URL=http://127.0.0.1:8545
CHAIN_ID=1337
DEPLOYER_MNEMONIC="..."      # required for --send
PQ_ALG=dilithium3            # or sphincs_shake_128s
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# Shared local helpers (contracts/tools/__init__.py)
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
    candidates: List[Path] = []
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


# ----------------------------- SDK wrappers ------------------------------ #


class _SdkError(RuntimeError):
    pass


class _Rpc:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url
        self.timeout = timeout
        self._client = None
        try:
            from omni_sdk.rpc.http import \
                HttpClient as _HttpClient  # type: ignore

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
                        payload = {
                            "jsonrpc": "2.0",
                            "id": self._id,
                            "method": method,
                            "params": params or [],
                        }
                        r = requests.post(self.url, json=payload, timeout=self.timeout)
                        r.raise_for_status()
                        data = r.json()
                        if "error" in data and data["error"]:
                            raise _SdkError(
                                f"RPC error {data['error'].get('code')}: {data['error'].get('message')}"
                            )
                        return data["result"]

                self._client = _ReqWrapper(url, timeout)
            except Exception as exc:
                raise _SdkError(
                    f"RPC init failed (install omni-sdk or requests): {exc}"
                ) from exc

    def call(self, method: str, params: Any = None) -> Any:
        return self._client.call(method, params or [])


class _Signer:
    def __init__(self, algo: str, account_index: int = 0):
        self.algo = (algo or "dilithium3").lower()
        self.account_index = account_index
        self._signer = None

    @classmethod
    def from_mnemonic(
        cls, mnemonic: str, algo: str = "dilithium3", account_index: int = 0
    ) -> "_Signer":
        s = cls(algo=algo, account_index=account_index)
        # generic
        try:
            from omni_sdk.wallet.signer import Signer  # type: ignore

            if hasattr(Signer, "from_mnemonic"):
                s._signer = Signer.from_mnemonic(
                    mnemonic, alg=algo, account_index=account_index
                )
                return s
        except Exception:
            pass
        # specific
        try:
            if algo.startswith("dilithium"):
                from omni_sdk.wallet.signer import \
                    Dilithium3Signer  # type: ignore

                s._signer = Dilithium3Signer.from_mnemonic(
                    mnemonic, account_index=account_index
                )
            else:
                from omni_sdk.wallet.signer import \
                    SphincsShake128sSigner  # type: ignore

                s._signer = SphincsShake128sSigner.from_mnemonic(
                    mnemonic, account_index=account_index
                )
        except Exception as exc:
            raise _SdkError(f"Signer init failed: {exc}") from exc
        return s

    @property
    def address(self) -> str:
        for attr in ("address", "addr", "bech32", "bech32m"):
            if hasattr(self._signer, attr):
                v = getattr(self._signer, attr)
                return v() if callable(v) else v
        raise _SdkError("Signer does not expose address()")

    def sign(self, sign_bytes: bytes) -> Dict[str, Any]:
        s = self._signer
        if hasattr(s, "sign"):
            out = s.sign(sign_bytes)
            if isinstance(out, dict):
                return out
            if isinstance(out, (tuple, list)) and len(out) == 2:
                sig, pub = out
                return {
                    "alg_id": getattr(s, "alg_id", self.algo),
                    "pubkey": pub,
                    "signature": sig,
                }
        for name in ("sign_detached", "sign_message"):
            if hasattr(s, name):
                sig = getattr(s, name)(sign_bytes)
                pub = getattr(s, "export_public_key", lambda: b"")()
                return {
                    "alg_id": getattr(s, "alg_id", self.algo),
                    "pubkey": pub,
                    "signature": sig,
                }
        raise _SdkError("Signer lacks compatible sign()")


# ----------------------------- ABI / Manifest ---------------------------- #


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise _SdkError(f"Failed to read JSON: {path}: {exc}") from exc


def _load_abi(
    manifest_path: Optional[Path], abi_path: Optional[Path]
) -> Dict[str, Any]:
    if manifest_path:
        m = _load_json(manifest_path)
        abi = m.get("abi")
        if not abi:
            raise _SdkError("manifest.json has no 'abi' field")
        return abi
    if abi_path:
        a = _load_json(abi_path)
        # allow either an ABI dict or an object with "abi"
        return a.get("abi", a)
    raise _SdkError("Provide --manifest or --abi")


# ----------------------------- Args parsing ------------------------------ #


def _parse_args_obj(
    args_json: Optional[str], kv_pairs: Sequence[str], pos_json: Optional[str]
) -> Tuple[List[Any], Dict[str, Any]]:
    # Priority: --pos (JSON array) and --args-json (object) first;
    # then add any --arg k=v pairs.
    pos: List[Any] = []
    kw: Dict[str, Any] = {}

    if pos_json:
        try:
            pos_val = json.loads(pos_json)
            if not isinstance(pos_val, list):
                raise ValueError("pos must be a JSON array")
            pos = pos_val
        except Exception as exc:
            raise _SdkError(f"--pos must be JSON array: {exc}") from exc

    if args_json:
        try:
            kw_val = json.loads(args_json)
            if not isinstance(kw_val, dict):
                raise ValueError("args-json must be an object")
            kw.update(kw_val)
        except Exception as exc:
            raise _SdkError(f"--args-json must be JSON object: {exc}") from exc

    for item in kv_pairs or []:
        if "=" not in item:
            # treat as positional if no '='
            pos.append(_lex_value(item))
            continue
        k, v = item.split("=", 1)
        kw[k] = _lex_value(v)

    return pos, kw


def _lex_value(s: str) -> Any:
    # Best-effort literal parser: try JSON, hex→bytes, int, float; else string.
    s = s.strip()
    # JSON object/array/number/bool/null
    if (
        (s.startswith("{") and s.endswith("}"))
        or (s.startswith("[") and s.endswith("]"))
        or s in ("true", "false", "null")
        or s.replace(".", "", 1).isdigit()
    ):
        try:
            return json.loads(s)
        except Exception:
            pass
    # hex → bytes
    if s.startswith("0x"):
        try:
            return binascii.unhexlify(s[2:])
        except Exception:
            pass
    # base64 → bytes
    if s.endswith("=") and len(s) % 4 == 0:
        try:
            return base64.b64decode(s)
        except Exception:
            pass
    # int
    try:
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s, 10)
    except Exception:
        pass
    # float
    try:
        if any(c in s for c in (".", "e", "E")):
            return float(s)
    except Exception:
        pass
    # fallback string
    return s


# ------------------------------ Read-only call --------------------------- #


def _readonly_call(
    rpc: _Rpc,
    chain_id: int,
    address: str,
    abi: Dict[str, Any],
    fn_name: str,
    pos_args: Sequence[Any],
    kw_args: Dict[str, Any],
    from_addr: Optional[str],
) -> Dict[str, Any]:
    """
    Prefer omni_sdk.contracts.client.ContractClient if present.
    Fallback to best-effort RPC 'simulate' if available (studio-services),
    else raise a clear error.
    """
    # Try ContractClient
    try:
        from omni_sdk.contracts.client import ContractClient  # type: ignore

        client = ContractClient(
            rpc_url=rpc.url, chain_id=chain_id, address=address, abi=abi
        )
        # Try common call method variations
        for name in ("call_readonly", "view", "call"):
            if hasattr(client, name):
                method = getattr(client, name)
                try:
                    # Accept both positional and keyword styles
                    if kw_args:
                        res = method(fn_name, *pos_args, **kw_args)
                    else:
                        res = method(fn_name, *pos_args)
                    return {
                        "ok": True,
                        "address": address,
                        "function": fn_name,
                        "return": res,
                    }
                except TypeError:
                    # Some clients expect {"args": {...}}
                    res = method(fn_name, {"args": kw_args or list(pos_args)})
                    return {
                        "ok": True,
                        "address": address,
                        "function": fn_name,
                        "return": res,
                    }
    except Exception as exc:
        # keep trying fallback paths
        last_err = exc
    else:
        last_err = None

    # Try a generic RPC simulate endpoint (if node or studio-services provide one)
    try:
        payload = {
            "address": address,
            "function": fn_name,
            "args": kw_args if kw_args else list(pos_args),
            "from": from_addr,
            "chainId": chain_id,
        }
        # Convention: rpc.call("state.simulateCall", [payload]) or "call.simulate"
        for m in ("state.simulateCall", "call.simulate", "contracts.simulate"):
            try:
                res = rpc.call(m, [payload])
                return {
                    "ok": True,
                    "address": address,
                    "function": fn_name,
                    "return": res,
                }
            except Exception:
                continue
    except Exception:
        pass

    raise _SdkError(
        "Read-only call failed: ContractClient/simulate endpoint not available. "
        "Install/upgrade omni-sdk or run against a node/services that supports simulation."
    )


# ------------------------------ Send (write) ----------------------------- #


def _send_call(
    rpc: _Rpc,
    chain_id: int,
    address: str,
    abi: Dict[str, Any],
    fn_name: str,
    pos_args: Sequence[Any],
    kw_args: Dict[str, Any],
    signer: _Signer,
    gas_price: Optional[int],
    gas_limit: Optional[int],
    value: Optional[int],
    wait: bool,
    timeout: float,
) -> Dict[str, Any]:
    """Build → sign → encode → send → (optional) await receipt."""
    sender = signer.address

    # 1) Nonce
    try:
        nonce = int(rpc.call("state.getNonce", [sender]))
    except Exception as exc:
        raise _SdkError(f"Failed to get nonce for {sender}: {exc}") from exc

    # 2) Build call tx via omni_sdk.tx.build
    try:
        from omni_sdk.tx import build as tx_build  # type: ignore

        build_fn = None
        for name in ("call", "build_call", "make_call"):
            if hasattr(tx_build, name):
                build_fn = getattr(tx_build, name)
                break
        if build_fn is None:
            raise _SdkError("omni_sdk.tx.build lacks call() builder; update SDK.")

        # Accept either positional or keyword mapping depending on ABI client surface
        args_payload: Any
        if kw_args:
            args_payload = kw_args
        else:
            args_payload = list(pos_args)

        # common signature flavors
        # (address, abi, function, args, sender, nonce, chain_id, gas_price=?, gas_limit=?, value=?)
        try:
            tx_obj = build_fn(
                address=address,
                abi=abi,
                function=fn_name,
                args=args_payload,
                sender=sender,
                nonce=nonce,
                chain_id=chain_id,
                gas_price=gas_price,
                gas_limit=gas_limit,
                value=value,
            )
        except TypeError:
            tx_obj = build_fn(
                address,
                abi,
                fn_name,
                args_payload,
                sender,
                nonce,
                chain_id,
                gas_price,
                gas_limit,
                value,
            )
    except Exception as exc:
        raise _SdkError(f"Failed to build call tx: {exc}") from exc

    # 3) Encode sign-bytes, sign, attach sig, encode CBOR
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

        if hasattr(tx_encode, "attach_signature"):
            signed = tx_encode.attach_signature(tx_obj, sig)  # type: ignore
        else:
            if isinstance(tx_obj, dict):
                tx_obj["signature"] = sig
                signed = tx_obj
            else:
                setattr(tx_obj, "signature", sig)
                signed = tx_obj

        # Encode to bytes
        tx_bytes = None
        for name in ("to_bytes", "encode", "to_cbor"):
            if hasattr(tx_encode, name):
                try:
                    tx_bytes = getattr(tx_encode, name)(signed)  # type: ignore
                    break
                except TypeError:
                    try:
                        tx_bytes = getattr(tx_encode, name)(signed, canonical=True)  # type: ignore
                        break
                    except Exception:
                        pass
        if tx_bytes is None:
            raise _SdkError("Could not encode signed tx to bytes")
    except Exception as exc:
        raise _SdkError(f"Signing/encoding failed: {exc}") from exc

    # 4) Send + await
    try:
        raw_hex = "0x" + binascii.hexlify(tx_bytes).decode()
        tx_hash = rpc.call("tx.sendRawTransaction", [raw_hex])
    except Exception as exc:
        raise _SdkError(f"tx.sendRawTransaction failed: {exc}") from exc

    receipt = None
    if wait:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                receipt = rpc.call("tx.getTransactionReceipt", [tx_hash])
                if receipt:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not receipt:
            raise _SdkError(f"Timed out waiting for receipt ({tx_hash})")

    return {
        "ok": True,
        "txHash": tx_hash,
        "from": sender,
        "to": address,
        "function": fn_name,
        "receipt": receipt,
    }


# ----------------------------------- CLI --------------------------------- #


def _parse_cli(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="contracts.tools.call",
        description="Call a contract method (read-only by default; use --send for a state-changing call).",
    )
    # Where/how
    p.add_argument(
        "--rpc", type=str, default=None, help="RPC URL (e.g., http://127.0.0.1:8545)"
    )
    p.add_argument("--chain-id", type=int, default=None, help="Chain ID (e.g., 1337)")
    p.add_argument(
        "--address",
        type=str,
        required=True,
        help="Contract address (bech32m anim1… or hex)",
    )

    # ABI / Manifest
    p.add_argument(
        "--manifest", type=Path, default=None, help="Path to manifest.json (preferred)"
    )
    p.add_argument(
        "--abi", type=Path, default=None, help="Path to abi.json (if no manifest)"
    )

    # Which function + args
    p.add_argument(
        "--fn",
        "--function",
        dest="fn",
        type=str,
        required=True,
        help="Function name to call",
    )
    p.add_argument(
        "--args-json",
        type=str,
        default=None,
        help='JSON object of named args, e.g. \'{"to":"anim1..","amount":1}\'',
    )
    p.add_argument(
        "--arg",
        action="append",
        default=[],
        help='Repeatable key=value or positional (no "=") to append (e.g. --arg 5 --arg to=anim1...)',
    )
    p.add_argument(
        "--pos",
        type=str,
        default=None,
        help="JSON array of positional args (alternative to --arg without '=')",
    )

    # Read-only vs send
    p.add_argument(
        "--send",
        action="store_true",
        help="Send a state-changing transaction instead of read-only simulation",
    )
    p.add_argument(
        "--value",
        type=int,
        default=None,
        help="Optional value to transfer (if supported by runtime)",
    )
    p.add_argument("--gas-price", type=int, default=None, help="Override gas price")
    p.add_argument("--gas-limit", type=int, default=None, help="Override gas limit")

    # Signing (for --send)
    p.add_argument(
        "--from",
        dest="from_addr",
        type=str,
        default=None,
        help="Sender address override (defaults to signer address)",
    )
    p.add_argument(
        "--mnemonic",
        type=str,
        default=None,
        help="Deployer mnemonic for signing (or DEPLOYER_MNEMONIC)",
    )
    p.add_argument(
        "--alg",
        type=str,
        default=None,
        help="PQ alg: dilithium3 (default) or sphincs_shake_128s",
    )
    p.add_argument(
        "--account-index", type=int, default=0, help="HD account index (default: 0)"
    )

    # Wait/Output
    p.add_argument("--wait", action="store_true", help="Wait for receipt (when --send)")
    p.add_argument("--timeout", type=float, default=90.0, help="Wait timeout (seconds)")
    p.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    return p.parse_args(argv)


def main(argv=None) -> int:
    _load_env()
    args = _parse_cli(argv)

    rpc_url = args.rpc or os.environ.get("RPC_URL")
    if not rpc_url:
        print("[call] ERROR: RPC URL not provided (--rpc or RPC_URL)", file=sys.stderr)
        return 2
    rpc = _Rpc(rpc_url, timeout=15.0)

    # Resolve chainId: CLI > ENV > RPC
    chain_id = args.chain_id or (int(os.environ.get("CHAIN_ID", "0")) or None)
    if chain_id is None:
        try:
            chain_id = int(rpc.call("chain.getChainId", []))
        except Exception:
            try:
                params = rpc.call("chain.getParams", [])
                chain_id = int(
                    params.get("chainId") or (params.get("chain") or {}).get("id")
                )
            except Exception as exc:
                print(
                    f"[call] ERROR: could not determine chainId via RPC: {exc}",
                    file=sys.stderr,
                )
                return 2

    # ABI / manifest
    try:
        abi = _load_abi(args.manifest, args.abi)
    except Exception as exc:
        print(f"[call] ERROR: {exc}", file=sys.stderr)
        return 2

    # Args
    try:
        pos_args, kw_args = _parse_args_obj(args.args_json, args.arg, args.pos)
    except Exception as exc:
        print(f"[call] ERROR parsing args: {exc}", file=sys.stderr)
        return 2

    # Mode
    if not args.send:
        try:
            res = _readonly_call(
                rpc=rpc,
                chain_id=int(chain_id),
                address=args.address,
                abi=abi,
                fn_name=args.fn,
                pos_args=pos_args,
                kw_args=kw_args,
                from_addr=args.from_addr,
            )
        except Exception as exc:
            print(f"[call] ERROR (readonly): {exc}", file=sys.stderr)
            return 3

        if args.json:
            print(canonical_json_str(res))
        else:
            print(f"return: {json.dumps(res.get('return'), ensure_ascii=False)}")
        return 0

    # --send path
    mnemonic = args.mnemonic or os.environ.get("DEPLOYER_MNEMONIC")
    if not mnemonic:
        print(
            "[call] ERROR: --send requires --mnemonic or DEPLOYER_MNEMONIC",
            file=sys.stderr,
        )
        return 2
    alg = (args.alg or os.environ.get("PQ_ALG") or "dilithium3").lower()
    try:
        signer = _Signer.from_mnemonic(
            mnemonic, algo=alg, account_index=args.account_index
        )
    except Exception as exc:
        print(f"[call] ERROR: signer init failed: {exc}", file=sys.stderr)
        return 2

    # Optional consistency: if --from provided and differs from signer, warn (stderr)
    if args.from_addr and args.from_addr != signer.address:
        print(
            f"[call] WARN: --from differs from signer address; using signer={signer.address}",
            file=sys.stderr,
        )

    try:
        res = _send_call(
            rpc=rpc,
            chain_id=int(chain_id),
            address=args.address,
            abi=abi,
            fn_name=args.fn,
            pos_args=pos_args,
            kw_args=kw_args,
            signer=signer,
            gas_price=args.gas_price,
            gas_limit=args.gas_limit,
            value=args.value,
            wait=bool(args.wait),
            timeout=float(args.timeout),
        )
    except Exception as exc:
        print(f"[call] ERROR (send): {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(canonical_json_str(res))
    else:
        print(f"txHash: {res.get('txHash')}")
        rcpt = res.get("receipt")
        if rcpt:
            print(
                f"status: {rcpt.get('status')}  gasUsed: {rcpt.get('gasUsed')}  block: {rcpt.get('blockNumber')}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
