from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import typer

# hard deps installed by setup.sh
import requests
import cbor2

# project PQ signer (matches your traceback)
from pq.py.sign import sign_detached as pq_sign_detached  # type: ignore


app = typer.Typer(no_args_is_help=True, help="Transaction commands (send, encode, etc.)")


# -----------------------------
# Errors / helpers
# -----------------------------

@dataclasses.dataclass
class RpcError(Exception):
    code: int
    message: str
    data: Any = None
    method: str = ""
    params: Any = None

    def __str__(self) -> str:
        base = f"RPC error {self.code}: {self.message}"
        if self.method:
            base += f" | method={self.method}"
        if self.data is not None:
            base += f" | data={self.data}"
        return base


def _as_int(v: Any) -> int:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.startswith(("0x", "0X")):
            return int(s, 16)
        return int(s)
    raise ValueError(f"Expected int-like, got {type(v)}: {v!r}")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_rpc_url() -> str:
    return os.environ.get("ANIMICA_RPC_URL", "http://127.0.0.1:18545/rpc")


def _wallets_path() -> pathlib.Path:
    p = os.environ.get("ANIMICA_WALLETS_PATH")
    if p:
        return pathlib.Path(p).expanduser()
    return pathlib.Path.home() / ".animica" / "wallets.json"


def _hex_to_bytes(h: str) -> bytes:
    s = h.strip()
    if s.startswith(("0x", "0X")):
        s = s[2:]
    if len(s) % 2 != 0:
        s = "0" + s
    return bytes.fromhex(s)


def _bytes_to_hex(b: bytes) -> str:
    return b.hex()


def _cbor_dumps(obj: Any, canonical: bool) -> bytes:
    # canonical=True is important if the node re-encodes before verifying
    return cbor2.dumps(obj, canonical=canonical)


def _rpc_post(rpc_url: str, payload: Dict[str, Any], timeout_s: float = 30.0) -> Dict[str, Any]:
    r = requests.post(rpc_url, json=payload, timeout=timeout_s)
    # If node returns non-JSON errors, surface them clearly
    try:
        j = r.json()
    except Exception:
        raise RuntimeError(f"Non-JSON RPC response ({r.status_code}): {r.text[:500]}")
    return j


def _rpc_call(rpc_url: str, method: str, params: Optional[List[Any]] = None, timeout_s: float = 30.0) -> Any:
    req = {
        "jsonrpc": "2.0",
        "id": _now_ms(),
        "method": method,
        "params": params or [],
    }
    j = _rpc_post(rpc_url, req, timeout_s=timeout_s)
    if "error" in j and j["error"] is not None:
        err = j["error"]
        raise RpcError(
            code=int(err.get("code", -32000)),
            message=str(err.get("message", "Unknown error")),
            data=err.get("data"),
            method=method,
            params=params or [],
        )
    return j.get("result")


def _try_rpc(rpc_url: str, method: str, params: Optional[List[Any]] = None) -> Tuple[bool, Any]:
    try:
        return True, _rpc_call(rpc_url, method, params=params)
    except RpcError:
        return False, None


# -----------------------------
# Wallet loading
# -----------------------------

def _load_wallet_db(path: pathlib.Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Wallet store not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _iter_wallet_entries(db: Any) -> List[Dict[str, Any]]:
    # Accept several shapes:
    #  - {"wallets":[...]}
    #  - [...]
    #  - {"label": {...}, "label2": {...}}
    if isinstance(db, dict) and "wallets" in db and isinstance(db["wallets"], list):
        return [e for e in db["wallets"] if isinstance(e, dict)]
    if isinstance(db, list):
        return [e for e in db if isinstance(e, dict)]
    if isinstance(db, dict):
        out: List[Dict[str, Any]] = []
        for k, v in db.items():
            if isinstance(v, dict):
                vv = dict(v)
                vv.setdefault("label", k)
                out.append(vv)
        return out
    return []


def _find_wallet_by_address(db: Any, address: str) -> Dict[str, Any]:
    addr = address.strip()
    for e in _iter_wallet_entries(db):
        a = str(e.get("address") or e.get("addr") or "").strip()
        if a == addr:
            return e
    raise KeyError(f"Address not found in wallet store: {addr}")


def _wallet_alg_name(entry: Dict[str, Any]) -> str:
    return str(entry.get("alg_name") or entry.get("alg") or entry.get("algorithm") or "dilithium3")


def _wallet_alg_id(entry: Dict[str, Any]) -> int:
    v = entry.get("alg_id") or entry.get("algId") or entry.get("algid")
    if v is None:
        # not strictly required for signing, but used in envelope
        return 0
    return _as_int(v)


def _wallet_pubkey(entry: Dict[str, Any]) -> bytes:
    h = entry.get("public_key_hex") or entry.get("pubkey_hex") or entry.get("publicKeyHex")
    if not h:
        raise KeyError("wallet entry missing public_key_hex")
    return _hex_to_bytes(str(h))


def _wallet_seckey(entry: Dict[str, Any]) -> bytes:
    h = entry.get("secret_key_hex") or entry.get("seckey_hex") or entry.get("secretKeyHex")
    if not h:
        raise KeyError("wallet entry missing secret_key_hex")
    return _hex_to_bytes(str(h))


# -----------------------------
# Tx building / signing
# -----------------------------

def _build_tx_body(
    from_addr: str,
    to_addr: str,
    value: int,
    nonce: int,
    max_fee: int,
    gas_limit: int,
    chain_id: int,
    data_hex: Optional[str],
) -> Dict[str, Any]:
    data_bytes = b""
    if data_hex:
        data_bytes = _hex_to_bytes(data_hex)

    # Keep keys stable (canonical CBOR also sorts map keys, but we still keep a consistent dict)
    body: Dict[str, Any] = {
        "to": to_addr,
        "from": from_addr,
        "value": int(value),
        "nonce": int(nonce),
        "maxFee": int(max_fee),
        "gasLimit": int(gas_limit),
        "data": data_bytes,
        "chainId": int(chain_id),
    }
    return body


def _sign_and_wrap(
    body: Dict[str, Any],
    alg_name: str,
    alg_id: int,
    pubkey: bytes,
    seckey: bytes,
    chain_id: int,
    domain: str,
    prehash: str,
    signable: bytes,
    canonical_wrap: bool = True,
) -> Tuple[bytes, str, bytes, bytes]:
    sig = pq_sign_detached(
        message=signable,
        algorithm=alg_name,
        secret_key=seckey,
        domain=domain,
        chain_id=chain_id,
        prehash=prehash,
    )

    sig_obj: Dict[str, Any] = {
        "pk": pubkey,
        "sig": sig,
        "alg": int(alg_id),
        "domain": domain,
        "prehash": prehash,
        "chainId": int(chain_id),
    }

    raw_obj: Dict[str, Any] = {
        "sig": sig_obj,
        "tx": body,
    }

    raw_tx = _cbor_dumps(raw_obj, canonical=canonical_wrap)
    raw_hex = _bytes_to_hex(raw_tx)
    return raw_tx, raw_hex, sig, signable


def _send_raw_hex(rpc_url: str, raw_hex: str) -> Any:
    # Node expects hex string (your traceback shows raw_hex passed)
    return _rpc_call(rpc_url, "tx.sendRawTransaction", [raw_hex])


def _sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def _sha3_256(b: bytes) -> bytes:
    return hashlib.sha3_256(b).digest()


def _make_signable_variants(body: Dict[str, Any]) -> List[Tuple[str, bytes]]:
    variants: List[Tuple[str, bytes]] = []
    # Most likely (node canonicalizes)
    variants.append(("cbor(canonical body)", _cbor_dumps(body, canonical=True)))
    # If node uses raw-map order (less likely)
    variants.append(("cbor(non-canonical body)", _cbor_dumps(body, canonical=False)))
    # If node signs nested form
    variants.append(("cbor(canonical {'tx': body})", _cbor_dumps({"tx": body}, canonical=True)))
    return variants


def _domain_candidates(alg_name: str, user_domain: Optional[str]) -> List[str]:
    if user_domain:
        return [user_domain]
    # Your repo has used both plain 'tx' and 'sig|dilithium3|tx' historically
    return [
        "tx",
        f"sig|{alg_name}|tx",
        "sig|tx",
        f"sig|{alg_name}|transaction",
        "transaction",
    ]


def _prehash_candidates(user_prehash: Optional[str]) -> List[str]:
    if user_prehash:
        return [user_prehash]
    # Try likely options; node-side verifier often canonicalizes + hashes
    return ["none", "sha256", "sha3-256"]


def _pretty_ctx(verbose: bool, rpc_url: str, chain_id: int, chain_id_source: str) -> None:
    if not verbose:
        return
    print()
    print("CHAIN CONTEXT DEBUG")
    print(f"  rpc_url: {rpc_url}")
    print(f"  chain_id: {chain_id}")
    print(f"  chain_id_source: {chain_id_source}")
    print()


def _pretty_sig_debug(verbose: bool, alg_name: str, chain_id: int, domain: str, prehash: str, pubkey: bytes, sig: bytes, signable: bytes) -> None:
    if not verbose:
        return
    print("PQ SIGNATURE DEBUG")
    print(f"  algorithm: {alg_name}")
    print(f"  domain: {domain}")
    print(f"  prehash: {prehash}")
    print(f"  chain_id_in_pq: {chain_id}")
    print(f"  pubkey_len: {len(pubkey)} bytes")
    print(f"  sig_len: {len(sig)} bytes")
    print(f"  message_len: {len(signable)} bytes")
    print(f"  message_prefix: {signable[:32].hex()}")
    print()


# -----------------------------
# CLI
# -----------------------------

@app.command("send")
def send(
    from_addr: str = typer.Option(..., "--from", help="Sender address (must exist in wallets.json)"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address"),
    value: int = typer.Option(..., "--value", help="Amount to send (integer)"),
    rpc_url: str = typer.Option(None, "--rpc-url", help="RPC URL (default: ANIMICA_RPC_URL or http://127.0.0.1:18545/rpc)"),
    chain_id: Optional[int] = typer.Option(None, "--chain-id", help="Chain ID override (int)"),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Nonce override (int)"),
    max_fee: int = typer.Option(1, "--max-fee", help="Max fee (default: 1)"),
    gas_limit: int = typer.Option(21000, "--gas-limit", help="Gas limit (default: 21000)"),
    data: Optional[str] = typer.Option(None, "--data", help="Hex calldata (0x...)"),
    domain: Optional[str] = typer.Option(None, "--domain", help="PQ signing domain override"),
    prehash: Optional[str] = typer.Option(None, "--prehash", help="PQ prehash override (none|sha256|sha3-256)"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose debug output"),
) -> None:
    rpc_url = rpc_url or _default_rpc_url()

    # chain id
    chain_id_source = "cli override" if chain_id is not None else ""
    if chain_id is None:
        ok, cid = _try_rpc(rpc_url, "chain.getChainId", [])
        if ok and cid is not None:
            chain_id = _as_int(cid)
            chain_id_source = "node:chain.getChainId"
        else:
            raise typer.Exit(code=2)

    assert chain_id is not None, "chain_id must be resolved"
    _pretty_ctx(verbose, rpc_url, chain_id, chain_id_source)

    # wallet keys
    db = _load_wallet_db(_wallets_path())
    entry = _find_wallet_by_address(db, from_addr)

    alg_name = _wallet_alg_name(entry)
    alg_id = _wallet_alg_id(entry)
    pubkey = _wallet_pubkey(entry)
    seckey = _wallet_seckey(entry)

    # nonce
    if nonce is None:
        # Your node supports state.getNonce (your log)
        ok, n = _try_rpc(rpc_url, "state.getNonce", [from_addr])
        if ok and n is not None:
            nonce = _as_int(n)
            if verbose:
                print("nonce: using state.getNonce")
        else:
            # last resort
            nonce = 0
            if verbose:
                print("nonce: defaulting to 0 (state.getNonce unavailable)")

    # fee / gas - your node log shows "default(1) => 1"
    if verbose:
        print(f"maxFee: using default({max_fee}) => {max_fee}")
        print()

    body = _build_tx_body(
        from_addr=from_addr,
        to_addr=to_addr,
        value=value,
        nonce=nonce,
        max_fee=max_fee,
        gas_limit=gas_limit,
        chain_id=chain_id,
        data_hex=data,
    )

    # Try sending with retries on signature verification failure
    doms = _domain_candidates(alg_name, domain)
    pres = _prehash_candidates(prehash)
    signables = _make_signable_variants(body)

    last_err: Optional[Exception] = None

    for signable_label, signable in signables:
        for dom in doms:
            for pre in pres:
                try:
                    raw_tx, raw_hex, sig, used_signable = _sign_and_wrap(
                        body=body,
                        alg_name=alg_name,
                        alg_id=alg_id,
                        pubkey=pubkey,
                        seckey=seckey,
                        chain_id=chain_id,
                        domain=dom,
                        prehash=pre,
                        signable=signable,
                        canonical_wrap=True,
                    )

                    _pretty_sig_debug(verbose, alg_name, chain_id, dom, pre, pubkey, sig, used_signable)

                    res = _send_raw_hex(rpc_url, raw_hex)

                    # Print result (hash or object)
                    print(json.dumps({"ok": True, "result": res}, indent=2))
                    return

                except RpcError as e:
                    last_err = e
                    # Signature verification failed on node
                    if e.code == -32012 and "post-quantum" in (e.message or "").lower():
                        if verbose:
                            print(f"[retry] signature rejected by node ({signable_label}, domain={dom}, prehash={pre})")
                        continue
                    # Chain id mismatch or other errors should stop immediately
                    if e.code == -32011:
                        raise
                    # If method not found etc, stop
                    raise

                except Exception as e:
                    last_err = e
                    continue

    # Nothing worked
    print("=== Transaction Failed ===")
    if isinstance(last_err, RpcError):
        print(f"Method:  {last_err.method}")
        print(f"Code:    {last_err.code}")
        print(f"Message: {last_err.message}")
        if last_err.data is not None:
            print(f"Data:    {last_err.data}")
    else:
        print(f"Error: {last_err!r}")

    raise typer.Exit(code=1)
