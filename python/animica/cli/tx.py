# python/animica/cli/tx.py
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import typer

# NOTE: requests is preferred here because you asked to include it in setup.sh
import requests

from animica.tx.signing import build_signable_tx_bytes

from pq.py import verify as pq_verify
from pq.py.sign import (
    Signature as PQSignature,
    family_from_algname,
    sign_detached as pq_sign_detached,
)

app = typer.Typer(help="Transaction commands")

COIN_SYMBOL = "ANM"
BASE_UNITS_PER_ANM = 1_000_000_000  # 1 ANM = 1e9 base units


# ----------------------------
# RPC
# ----------------------------

@dataclass(frozen=True)
class RpcError(Exception):
    method: str
    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        return f"RPC error {self.code}: {self.message} | data={self.data!r} (method={self.method})"


def _rpc_post(url: str, method: str, params: list[Any]) -> Any:
    payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 1_000_000_000, "method": method, "params": params}
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()
    j = r.json()
    if "error" in j and j["error"] is not None:
        err = j["error"]
        raise RpcError(method=method, code=int(err.get("code", -1)), message=str(err.get("message", "RPC error")), data=err.get("data"))
    return j.get("result")


def _rpc_call(url: str, method: str, params: list[Any]) -> Any:
    """
    Node seems to accept dot-style (chain.getChainId) in your logs.
    Spec/openrpc.json uses underscore-style (chain_getChainId).
    Try both.
    """
    try:
        return _rpc_post(url, method, params)
    except RpcError as e:
        # method not found -> try alias
        if e.code == -32601:
            alt = method.replace(".", "_") if "." in method else method.replace("_", ".")
            if alt != method:
                return _rpc_post(url, alt, params)
        raise


# ----------------------------
# Wallet loading
# ----------------------------

def _default_wallet_file() -> Path:
    return Path(os.environ.get("ANIMICA_WALLET_FILE", str(Path.home() / ".animica" / "wallets.json")))


def _load_wallets(wallet_file: Path) -> list[dict]:
    if not wallet_file.exists():
        return []
    try:
        data = json.loads(wallet_file.read_text())
    except Exception:
        return []
    if isinstance(data, dict) and "wallets" in data and isinstance(data["wallets"], list):
        return [w for w in data["wallets"] if isinstance(w, dict)]
    if isinstance(data, list):
        return [w for w in data if isinstance(w, dict)]
    return []


def _norm(s: Any) -> str:
    return str(s).strip()


def _find_wallet_entry(wallets: list[dict], from_arg: str) -> Tuple[str, dict]:
    """
    from_arg can be:
      - label (e.g. "test1")
      - bech32-like address (e.g. "anim1...")
    """
    key = _norm(from_arg)
    # 1) exact label match
    for w in wallets:
        if _norm(w.get("label")) == key:
            addr = _norm(w.get("address") or w.get("addr"))
            if not addr:
                raise typer.Exit(f"Wallet label {key!r} has no address in {w!r}")
            return addr, w
    # 2) exact address match
    for w in wallets:
        addr = _norm(w.get("address") or w.get("addr"))
        if addr == key:
            return addr, w
    raise typer.Exit(f"Sender {key!r} not found in wallet file. Create it with: animica wallet create --label <name>")


@dataclass(frozen=True)
class WalletKeys:
    alg_name: str
    alg_id: int
    public_key: bytes
    secret_key: bytes


def _parse_alg(entry: dict) -> Tuple[str, int]:
    # Accept many shapes
    alg_name = _norm(entry.get("alg_name") or entry.get("alg") or entry.get("algorithm") or "dilithium3").lower()
    alg_id_raw = entry.get("alg_id") or entry.get("algId") or entry.get("algorithm_id") or entry.get("id")

    alg_id: int
    if isinstance(alg_id_raw, int):
        alg_id = int(alg_id_raw)
    elif isinstance(alg_id_raw, str) and alg_id_raw.strip().lower().startswith("0x"):
        alg_id = int(alg_id_raw.strip(), 16)
    elif isinstance(alg_id_raw, str) and alg_id_raw.strip().isdigit():
        alg_id = int(alg_id_raw.strip())
    else:
        # common ids in this repo (dilithium3 = 0x1001)
        alg_id = 0x1001 if alg_name == "dilithium3" else 0

    return alg_name, alg_id


def _extract_wallet_keys(entry: dict) -> WalletKeys:
    alg_name, alg_id = _parse_alg(entry)

    pk_hex = entry.get("public_key_hex") or entry.get("publicKeyHex") or entry.get("pubkey_hex") or entry.get("pubkey")
    sk_hex = entry.get("secret_key_hex") or entry.get("secretKeyHex") or entry.get("privkey_hex") or entry.get("secret")

    if isinstance(pk_hex, str):
        public_key = bytes.fromhex(pk_hex.strip())
    elif isinstance(pk_hex, (bytes, bytearray)):
        public_key = bytes(pk_hex)
    else:
        raise typer.Exit("Wallet entry missing public key hex.")

    if isinstance(sk_hex, str):
        secret_key = bytes.fromhex(sk_hex.strip())
    elif isinstance(sk_hex, (bytes, bytearray)):
        secret_key = bytes(sk_hex)
    else:
        raise typer.Exit("Wallet entry missing secret key hex.")

    return WalletKeys(alg_name=alg_name, alg_id=alg_id, public_key=public_key, secret_key=secret_key)


# ----------------------------
# Units
# ----------------------------

def to_base_units(value_anm: Union[str, float, Decimal]) -> int:
    try:
        d = Decimal(str(value_anm))
    except (InvalidOperation, ValueError):
        raise typer.Exit(f"Invalid --value {value_anm!r}")
    if d < 0:
        raise typer.Exit("--value must be >= 0")
    # floor to 9 decimals
    q = d.quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
    return int(q * BASE_UNITS_PER_ANM)


def _fmt_anm(value_base: int) -> str:
    d = Decimal(value_base) / Decimal(BASE_UNITS_PER_ANM)
    # avoid scientific notation
    return format(d, "f")


# ----------------------------
# Chain / fee helpers
# ----------------------------

@dataclass(frozen=True)
class NetCfg:
    name: str
    rpc_url: str
    chain_id: int


def _load_network_config_fallback() -> NetCfg:
    # Default to testnet since your logs show that path.
    return NetCfg(
        name=os.environ.get("ANIMICA_NETWORK", "testnet"),
        rpc_url=os.environ.get("ANIMICA_RPC_URL", "http://127.0.0.1:18546/rpc"),
        chain_id=int(os.environ.get("ANIMICA_CHAIN_ID", "2")),
    )


def _resolve_rpc_url(rpc_url_opt: Optional[str]) -> str:
    if rpc_url_opt:
        return rpc_url_opt
    # try canonical env
    env = os.environ.get("ANIMICA_RPC_URL")
    if env:
        return env
    # fallback
    return _load_network_config_fallback().rpc_url


def _resolve_chain_id(rpc_url: str, override: Optional[int]) -> Tuple[int, str]:
    if override is not None:
        return int(override), "cli override"
    # ask node (preferred)
    try:
        cid = _rpc_call(rpc_url, "chain.getChainId", [])
        return int(cid), "node:chain.getChainId"
    except Exception:
        cfg = _load_network_config_fallback()
        return int(cfg.chain_id), "network config fallback"


def _get_nonce(rpc_url: str, addr: str) -> Tuple[int, str]:
    # prefer Animica-native nonce method (matches your logs)
    try:
        n = _rpc_call(rpc_url, "state.getNonce", [addr])
        return int(n or 0), "state.getNonce"
    except RpcError as e:
        if e.code != -32601:
            raise
    # legacy/compat fallback
    try:
        n = _rpc_call(rpc_url, "state.getTransactionCount", [addr])
        return int(n or 0), "state.getTransactionCount"
    except Exception:
        return 0, "default(0)"


def _deep_find_fee_floor(obj: Any) -> Optional[int]:
    """
    Best-effort heuristic: try to find some integer-like minimum fee in chain params.
    If not found, we return None and caller uses a safe default.
    """
    candidates: List[int] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                ks = str(k).lower()
                if any(t in ks for t in ["feefloor", "fee_floor", "minfee", "min_fee", "txfee", "fee"]):
                    if isinstance(v, int) and v >= 0:
                        candidates.append(int(v))
                    if isinstance(v, str) and v.strip().isdigit():
                        candidates.append(int(v.strip()))
                walk(v)
        elif isinstance(x, list):
            for it in x:
                walk(it)

    walk(obj)
    if not candidates:
        return None
    # choose the smallest positive non-zero if present, else 0
    positives = [c for c in candidates if c > 0]
    return min(positives) if positives else 0


def _suggest_max_fee(rpc_url: str) -> Tuple[int, str]:
    # try chain params
    try:
        params = _rpc_call(rpc_url, "chain.getParams", [])
        ff = _deep_find_fee_floor(params)
        if ff is not None:
            # add a little safety margin
            return max(int(ff), 1), "chain.getParams heuristic"
    except Exception:
        pass
    # no fee RPCs on your node -> default
    return 1, "default(1)"


# ----------------------------
# Signing + envelope
# ----------------------------

def _domain_candidates_for_alg(alg_name: str) -> list[str]:
    """Return signing-domain candidates.

    Canonical domain for tx signing is "tx".
    The PQ signing layer expands it into a chain-bound path like
    "sig|<family>|tx". We keep a couple legacy spellings for compatibility.
    """
    fam = alg_name
    try:
        fam = family_from_algname(alg_name)
    except Exception:
        fam = alg_name

    cands = ["tx", f"sig|{fam}|tx", f"sig|{alg_name}|tx"]
    out: list[str] = []
    for d in cands:
        if d not in out:
            out.append(d)
    return out


def _prehash_candidates() -> list[str]:
    # spec/domains.yaml defaultHash is sha3_256, but existing pq.py.sign defaults to sha3-512.
    return ["sha3-256", "sha3-512"]


def _chain_id_candidates(resolved_chain_id: int | None) -> list[int]:
    """Return chain-id candidates.

    Animica domain-tag signing requires a chain_id, so we never emit None.
    """
    if resolved_chain_id is None:
        return [0, 1, 2, 1337]
    try:
        return [int(resolved_chain_id)]
    except Exception:
        return [0, 1, 2, 1337]


def _build_signed_envelope(
    body: dict,
    wallet: WalletKeys,
    resolved_chain_id: int,
    *,
    verbose: bool,
) -> Tuple[bytes, dict]:
    """
    Build CBOR rawTx bytes (envelope) and return debug info.
    Implements signing fallbacks to match whatever the node verifies.
    """
    signable = build_signable_tx_bytes(body, chain_id=resolved_chain_id)

    last_err: Optional[Exception] = None
    attempts: list[dict] = []

    for domain in _domain_candidates_for_alg(wallet.alg_name):
        for prehash in _prehash_candidates():
            for cid in _chain_id_candidates(resolved_chain_id):
                try:
                    sig_env: PQSignature = pq_sign_detached(
                        signable,
                        wallet.alg_name,
                        wallet.secret_key,
                        domain=domain,
                        chain_id=cid,
                        context=b"",
                        prehash=prehash,  # type: ignore[arg-type]
                    )
                    # local verify for sanity
                    ok = pq_verify.verify_detached(
                        signable,
                        sig_env,
                        wallet.public_key,
                        chain_id=cid,
                    )
                    if not ok:
                        raise RuntimeError("local PQ verification failed (unexpected)")

                    envelope = {
                        "body": body,
                        # keep this minimal; nodes tend to be schema-strict
                        "sig": {
                            "alg_id": int(sig_env.alg_id),
                            "pk": wallet.public_key,
                            "sig": sig_env.sig,
                            # include these only if node expects them; if not, they should be ignored
                            "domain": sig_env.domain,
                            "prehash": sig_env.prehash,
                        },
                    }

                    import cbor2  # ensured by setup.sh
                    raw_tx = cbor2.dumps(envelope, canonical=True)

                    debug = {
                        "domain": domain,
                        "prehash": prehash,
                        "chain_id_in_pq": cid,
                        "signable_len": len(signable),
                        "signable_prefix": signable[:16].hex(),
                        "sig_len": len(sig_env.sig),
                        "pk_len": len(wallet.public_key),
                        "raw_tx_len": len(raw_tx),
                    }
                    attempts.append(debug)

                    return raw_tx, {"selected": debug, "attempts": attempts}

                except Exception as e:
                    last_err = e
                    attempts.append({"domain": domain, "prehash": prehash, "chain_id_in_pq": cid, "error": str(e)})
                    continue

    raise typer.Exit(f"Failed to build a locally-verifiable PQ signature. Last error: {last_err}. Attempts: {attempts}")


def _looks_like_sig_error(e: RpcError) -> bool:
    msg = (e.message or "").lower()
    return any(s in msg for s in ["signature", "verification", "post-quantum", "pq"])


# ----------------------------
# CLI
# ----------------------------

@app.command()
def send(
    from_addr: str = typer.Option(..., "--from", help="Sender label or address"),
    to_addr: str = typer.Option(..., "--to", help="Destination address"),
    value: str = typer.Option(..., "--value", help=f"Amount in {COIN_SYMBOL} (decimal ok)"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar="ANIMICA_RPC_URL", help="RPC URL"),
    chain_id: Optional[int] = typer.Option(None, "--chain-id", help="Override chain id"),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Override nonce"),
    gas: Optional[int] = typer.Option(None, "--gas", help="Gas limit (default 21000)"),
    max_fee: Optional[int] = typer.Option(None, "--max-fee", help="Max fee in base units (per tx)"),
    data_hex: Optional[str] = typer.Option(None, "--data-hex", help="Optional calldata as hex (0x...)"),
    wallet_file: Path = typer.Option(_default_wallet_file(), "--wallet-file", help="Wallet JSON file"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build/sign but do not broadcast"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose debug output"),
) -> None:
    url = _resolve_rpc_url(rpc_url)
    resolved_chain_id, chain_src = _resolve_chain_id(url, chain_id)

    if verbose:
        typer.echo("")
        typer.echo("CHAIN CONTEXT DEBUG", err=True)
        typer.echo(f"  rpc_url: {url}", err=True)
        typer.echo(f"  chain_id: {resolved_chain_id}", err=True)
        typer.echo(f"  chain_id_source: {chain_src}", err=True)
        typer.echo("")

    wallets = _load_wallets(wallet_file)
    sender_address, sender_entry = _find_wallet_entry(wallets, from_addr)
    wallet_keys = _extract_wallet_keys(sender_entry)

    if nonce is None:
        nonce_val, nonce_src = _get_nonce(url, sender_address)
    else:
        nonce_val, nonce_src = int(nonce), "cli override"

    if gas is None:
        gas_val = 21000
    else:
        gas_val = int(gas)

    if max_fee is None:
        max_fee_val, fee_src = _suggest_max_fee(url)
    else:
        max_fee_val, fee_src = int(max_fee), "cli override"

    value_units = to_base_units(value)

    if data_hex:
        dh = data_hex.strip().lower()
        if dh.startswith("0x"):
            dh = dh[2:]
        data_bytes = bytes.fromhex(dh) if dh else b""
    else:
        data_bytes = b""

    body = {
        "chainId": int(resolved_chain_id),
        "from": str(sender_address),
        "to": str(to_addr),
        "nonce": int(nonce_val),
        "value": int(value_units),
        "gasLimit": int(gas_val),
        "maxFee": int(max_fee_val),
        "data": data_bytes,
    }

    if verbose:
        typer.echo(f"nonce: using {nonce_src}", err=True)
        typer.echo(f"maxFee: using {fee_src} => {max_fee_val}", err=True)
        typer.echo("", err=True)

    # Build raw signed CBOR tx (with signing fallbacks)
    raw_tx, sig_debug = _build_signed_envelope(body, wallet_keys, resolved_chain_id, verbose=verbose)

    if verbose:
        sel = sig_debug.get("selected", {})
        typer.echo("PQ SIGNATURE DEBUG", err=True)
        typer.echo(f"  algorithm: {wallet_keys.alg_name} (id={wallet_keys.alg_id})", err=True)
        typer.echo(f"  domain: {sel.get('domain')}", err=True)
        typer.echo(f"  prehash: {sel.get('prehash')}", err=True)
        typer.echo(f"  chain_id_in_pq: {sel.get('chain_id_in_pq')}", err=True)
        typer.echo(f"  pubkey_len: {sel.get('pk_len')} bytes", err=True)
        typer.echo(f"  sig_len: {sel.get('sig_len')} bytes", err=True)
        typer.echo(f"  message_len: {sel.get('signable_len')} bytes", err=True)
        typer.echo(f"  message_prefix: {sel.get('signable_prefix')}", err=True)
        typer.echo("", err=True)

    raw_hex = "0x" + raw_tx.hex()

    if dry_run:
        typer.echo("=== Dry-Run Mode ===")
        typer.echo(f"From: {sender_address}")
        typer.echo(f"To:   {to_addr}")
        typer.echo(f"Value: {_fmt_anm(value_units)} {COIN_SYMBOL} ({value_units} base units)")
        typer.echo(f"Nonce: {nonce_val}")
        typer.echo(f"GasLimit: {gas_val}")
        typer.echo(f"MaxFee: {max_fee_val}")
        typer.echo(f"Chain ID: {resolved_chain_id}")
        typer.echo(f"Raw Size: {len(raw_tx)} bytes")
        typer.echo(f"RAW_TX={raw_hex}")
        typer.echo("\n✓ Transaction built and signed (not broadcast)")
        return

    # Broadcast, retrying *only* on signature-style failures by re-signing with the next fallback combo
    # (We already selected a locally-verifiable combo, but node may be using different domain/prehash/chain-binding.)
    try:
        tx_hash = _rpc_call(url, "tx.sendRawTransaction", [raw_hex])
        typer.echo("=== Transaction Submitted ===")
        typer.echo(f"Tx Hash: {tx_hash}")
        typer.echo(f"From: {sender_address}")
        typer.echo(f"To:   {to_addr}")
        typer.echo(f"Value: {_fmt_anm(value_units)} {COIN_SYMBOL}")
        typer.echo("\n✓ Transaction broadcast successfully")
        return
    except RpcError as e:
        # If it's a signature error, try the remaining signing fallbacks in order:
        if _looks_like_sig_error(e):
            # We already tried all fallbacks locally; re-run them but broadcasting each attempt until success.
            # This is intentionally conservative: only retries on explicit signature errors.
            wallets = _load_wallets(wallet_file)
            sender_address, sender_entry = _find_wallet_entry(wallets, from_addr)
            wallet_keys = _extract_wallet_keys(sender_entry)

            signable = build_signable_tx_bytes(body, chain_id=resolved_chain_id)

            import cbor2

            for domain in _domain_candidates_for_alg(wallet_keys.alg_name):
                for prehash in _prehash_candidates():
                    for cid in _chain_id_candidates(resolved_chain_id):
                        try:
                            sig_env = pq_sign_detached(
                                signable,
                                wallet_keys.alg_name,
                                wallet_keys.secret_key,
                                domain=domain,
                                chain_id=cid,
                                context=b"",
                                prehash=prehash,  # type: ignore[arg-type]
                            )
                        except Exception as e:
                            attempts.append({"domain": domain, "prehash": prehash, "chain_id_in_pq": cid, "error": str(e)})
                            continue
                        ok = pq_verify.verify_detached(signable, sig_env, wallet_keys.public_key, chain_id=cid)
                        if not ok:
                            continue
                        env = {
                            "body": body,
                            "sig": {
                                "alg_id": int(sig_env.alg_id),
                                "pk": wallet_keys.public_key,
                                "sig": sig_env.sig,
                                "domain": sig_env.domain,
                                "prehash": sig_env.prehash,
                            },
                        }
                        raw_try = cbor2.dumps(env, canonical=True)
                        raw_try_hex = "0x" + raw_try.hex()
                        try:
                            tx_hash = _rpc_call(url, "tx.sendRawTransaction", [raw_try_hex])
                            if verbose:
                                typer.echo(
                                    f"[retry] accepted with domain={domain} prehash={prehash} chain_id_in_pq={cid}",
                                    err=True,
                                )
                            typer.echo("=== Transaction Submitted ===")
                            typer.echo(f"Tx Hash: {tx_hash}")
                            typer.echo(f"From: {sender_address}")
                            typer.echo(f"To:   {to_addr}")
                            typer.echo(f"Value: {_fmt_anm(value_units)} {COIN_SYMBOL}")
                            typer.echo("\n✓ Transaction broadcast successfully")
                            return
                        except RpcError as e2:
                            if not _looks_like_sig_error(e2):
                                raise

        typer.echo("=== Transaction Failed ===", err=True)
        typer.echo(f"Method:  {e.method}", err=True)
        typer.echo(f"Code:    {e.code}", err=True)
        typer.echo(f"Message: {e.message}", err=True)
        if e.data is not None:
            typer.echo(f"Data:    {e.data}", err=True)
        raise typer.Exit(1)


@app.command()
def simulate(
    raw_tx_hex: str = typer.Option(..., "--raw-tx", help="0x-prefixed raw CBOR tx hex"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar="ANIMICA_RPC_URL", help="RPC URL"),
) -> None:
    """
    Best-effort simulation (only works if the node exposes a simulation/call method).
    """
    url = _resolve_rpc_url(rpc_url)
    try:
        # not guaranteed to exist; this is intentionally “best effort”
        res = _rpc_call(url, "tx.simulateRawTransaction", [raw_tx_hex])
        typer.echo(json.dumps(res, indent=2))
    except RpcError as e:
        typer.echo(f"Simulation not available on this node: {e}", err=True)
        raise typer.Exit(1)
