"""
animica.cli.tx — Transaction subcommands.

Key fix (PQ tx send):
- Build a tx with omni_sdk (to preserve the node-compatible envelope shape).
- Decode the envelope to a Python map.
- Compute canonical sign-bytes using animica.tx.signing.build_signable_tx_bytes(envelope).
- Produce a PQ signature using pq.py.sign.sign_detached (Animica domain-tag signing).
- Patch the *existing* signature slot inside the envelope, re-encode CBOR, and broadcast.

This fixes common failures:
- "No module named 'cbor2'" (now surfaced with a clear install hint)
- "a bytes-like object is required, not 'str'" (raw_tx may be hex string; we coerce)
- "Invalid post-quantum signature: verification failed" (sign bytes are canonical + chain-bound)
"""

from __future__ import annotations

import dataclasses
import json
import os
import string
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

import typer

from animica.coin import COIN_SYMBOL, UNIT_LABEL, to_base_units
from animica.config import load_network_config

app = typer.Typer(help="Transaction operations (build, sign, send, simulate)")
getcontext().prec = 28

_HEXCHARS = set(string.hexdigits)


# -----------------------------------------------------------------------------
# RPC helpers
# -----------------------------------------------------------------------------
try:
    from omni_sdk.rpc.http import RpcClient  # type: ignore

    HAVE_RPC = True
except Exception:
    HAVE_RPC = False


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    if rpc_url:
        return rpc_url
    cfg = load_network_config()
    return cfg.rpc_url


def _request_rpc(method: str, params: Optional[list], rpc_url: Optional[str]) -> Any:
    url = _resolve_rpc_url(rpc_url)
    if HAVE_RPC:
        client = RpcClient(url, timeout=10.0)  # type: ignore[name-defined]
        return client.request(method, params or [])

    # fallback
    import httpx  # lazy import

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    resp = httpx.post(url, json=payload, timeout=10.0)
    resp.raise_for_status()
    parsed = resp.json()
    if "error" in parsed:
        raise RuntimeError(parsed.get("error"))
    return parsed.get("result")


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


# -----------------------------------------------------------------------------
# Safety / PQ availability
# -----------------------------------------------------------------------------
def _warn_if_unsafe_pq_mode() -> None:
    if os.environ.get("ANIMICA_UNSAFE_PQ_FAKE") == "1":
        typer.echo("⚠️ WARNING: Using ANIMICA_UNSAFE_PQ_FAKE=1 mode", err=True)
        typer.echo("  This is NOT SECURE and should only be used for development/testing.", err=True)
        typer.echo("  Install liboqs-python for production use.", err=True)
        typer.echo("", err=True)


# -----------------------------------------------------------------------------
# Chain ID resolution
# -----------------------------------------------------------------------------
def resolve_chain_id(
    rpc_url: Optional[str],
    cli_chain_id: Optional[int],
    config_chain_id: Optional[int] = None,
) -> Tuple[int, str]:
    chain_id_to_use: Optional[int] = None
    chain_id_source: Optional[str] = None

    if cli_chain_id is not None:
        chain_id_to_use = cli_chain_id
        chain_id_source = "CLI/env"
    elif config_chain_id is not None:
        chain_id_to_use = config_chain_id
        chain_id_source = "network config"

    try:
        node_chain_id_result = _request_rpc("chain.getChainId", [], rpc_url)
        node_chain_id = int(node_chain_id_result) if node_chain_id_result is not None else None
    except Exception as e:
        typer.echo(f"Error: Could not query node's chain ID: {e}", err=True)
        typer.echo("Ensure the node is running and accessible via RPC.", err=True)
        raise typer.Exit(1)

    if node_chain_id is None:
        typer.echo("Error: Node returned invalid chain ID (null/empty)", err=True)
        raise typer.Exit(1)

    if chain_id_to_use is None:
        return node_chain_id, "node auto-detect"

    if int(chain_id_to_use) == int(node_chain_id):
        return int(chain_id_to_use), str(chain_id_source)

    typer.echo("=" * 60, err=True)
    typer.echo("Error: Chain ID mismatch", err=True)
    typer.echo("=" * 60, err=True)
    typer.echo(f"Source: {chain_id_source}", err=True)
    typer.echo(f"Specified ID: {chain_id_to_use}", err=True)
    typer.echo(f"Node chain ID: {node_chain_id}", err=True)
    typer.echo("", err=True)
    typer.echo("Solutions:", err=True)
    typer.echo(f" 1) Remove --chain-id to auto-detect (node: {node_chain_id})", err=True)
    typer.echo(f" 2) Set --chain-id {node_chain_id} to match the node", err=True)
    typer.echo(" 3) Unset ANIMICA_CHAIN_ID if set", err=True)
    typer.echo(" 4) Point at a different node with --rpc-url", err=True)
    typer.echo("=" * 60, err=True)
    raise typer.Exit(1)


def debug_chain_context(network_name: str, rpc_url: str, chain_id: int, chain_id_source: str) -> None:
    typer.echo("", err=True)
    typer.echo("CHAIN CONTEXT DEBUG", err=True)
    typer.echo(f"  network: {network_name}", err=True)
    typer.echo(f"  rpc_url:  {rpc_url}", err=True)
    typer.echo(f"  chain_id: {chain_id}", err=True)
    typer.echo(f"  chain_id_source: {chain_id_source}", err=True)
    typer.echo("", err=True)


# -----------------------------------------------------------------------------
# Wallet helpers
# -----------------------------------------------------------------------------
def _get_wallet_path(wallet_file: Optional[Path]) -> Path:
    if wallet_file is not None:
        return Path(wallet_file)
    env_path = os.environ.get("ANIMICA_WALLETS_FILE")
    if env_path:
        return Path(env_path)
    return Path.home() / ".animica" / "wallets.json"


def _resolve_sender(identifier: str, wallet_file: Optional[Path]) -> Tuple[str, Any]:
    # local CLI helpers
    from animica.cli.wallet import _find_wallet, _load_store  # type: ignore

    wallet_path = _get_wallet_path(wallet_file)
    if not wallet_path.exists():
        typer.echo(f"Error: Wallet store not found at {wallet_path}", err=True)
        typer.echo("Create a wallet with: animica wallet create --label <name>", err=True)
        raise typer.Exit(1)

    store = _load_store(wallet_path)
    try:
        wallet_entry = _find_wallet(store, identifier=identifier)
        return wallet_entry.address, wallet_entry
    except typer.Exit:
        if identifier.startswith("anim1"):
            typer.echo(f"Error: Address {identifier} not found in wallet", err=True)
        else:
            typer.echo(f"Error: Wallet label '{identifier}' not found", err=True)
        raise


def _resolve_destination(addr: str) -> str:
    if not addr or not isinstance(addr, str):
        typer.echo("Error: destination address is required", err=True)
        raise typer.Exit(1)
    if not addr.startswith("anim1"):
        typer.echo(f"Error: invalid destination address '{addr}' (must start with 'anim1')", err=True)
        raise typer.Exit(1)

    # Optional strict validation if PQ address module exists
    try:
        from pq.py.address import validate_address  # type: ignore

        validate_address(addr, expect_hrp="anim")
    except ImportError:
        pass
    except Exception as e:
        if os.environ.get("ANIMICA_UNSAFE_PQ_FAKE") == "1":
            typer.echo(f"⚠️ Warning: skipping strict address validation (unsafe mode): {e}", err=True)
        else:
            typer.echo(f"Error: invalid destination address: {e}", err=True)
            raise typer.Exit(1)

    return addr


def _hex_to_bytes(s: str) -> bytes:
    h = s.strip()
    if h.startswith(("0x", "0X")):
        h = h[2:]
    if not h:
        return b""
    if any(c not in _HEXCHARS for c in h):
        raise ValueError("string is not hex")
    if len(h) % 2 == 1:
        h = "0" + h
    return bytes.fromhex(h)


def _coerce_cbor_input(raw: Any) -> bytes:
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return bytes(raw)
    if isinstance(raw, str):
        s = raw.strip()
        # most common: "0x..." hex
        try:
            return _hex_to_bytes(s)
        except Exception:
            pass
        # last resort: treat as utf-8 bytes
        return s.encode("utf-8")
    raise TypeError(f"raw tx must be bytes-like or hex string; got {type(raw).__name__}")


def _coerce_mapping(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    return obj


# -----------------------------------------------------------------------------
# CBOR envelope patching (robust to a few shapes / str or bytes keys)
# -----------------------------------------------------------------------------
def _patch_sig_in_envelope(env: Any, *, sig_bytes: bytes, alg_id: int, domain: str, prehash: str) -> None:
    """
    Try to patch common envelope layouts:
      - {"sig": {...}} or {"sig": b"..."}
      - {"signature": {...}} / {"signature": b"..."}
      - {"sigs": [ {...}, ... ]}

    Keys may be text or bytes (CBOR).
    """
    if not isinstance(env, dict):
        raise TypeError("raw tx envelope must decode to a CBOR map/dict")

    def _k(s: str) -> Tuple[str, bytes]:
        return s, s.encode("utf-8")

    def _get_key(m: dict, name: str) -> Optional[Any]:
        ks, kb = _k(name)
        if ks in m:
            return ks
        if kb in m:
            return kb
        return None

    def patch_sig_obj(sig_obj: Any) -> Any:
        if isinstance(sig_obj, (bytes, bytearray)):
            return bytes(sig_bytes)

        if isinstance(sig_obj, dict):
            # signature bytes field
            for cand in ("sig", "signature", "bytes"):
                key = _get_key(sig_obj, cand)
                if key is not None:
                    sig_obj[key] = sig_bytes
                    break
            else:
                # default
                ks, _ = _k("sig")
                sig_obj[ks] = sig_bytes

            # metadata (only overwrite if present)
            for cand in ("alg_id", "algId", "alg"):
                key = _get_key(sig_obj, cand)
                if key is not None:
                    sig_obj[key] = alg_id
            key = _get_key(sig_obj, "domain")
            if key is not None:
                sig_obj[key] = domain
            key = _get_key(sig_obj, "prehash")
            if key is not None:
                sig_obj[key] = prehash
            return sig_obj

        # unknown type -> replace
        return {"alg_id": alg_id, "domain": domain, "prehash": prehash, "sig": sig_bytes}

    for top in ("sig", "signature"):
        key = _get_key(env, top)
        if key is not None:
            env[key] = patch_sig_obj(env[key])
            return

    key = _get_key(env, "sigs")
    if key is not None and isinstance(env[key], list) and env[key]:
        env[key][0] = patch_sig_obj(env[key][0])
        return

    # If we can't find a signature slot, create a conservative one
    env["sig"] = {"alg_id": alg_id, "domain": domain, "prehash": prehash, "sig": sig_bytes}


def _decode_envelope(raw_tx_template: Any) -> dict:
    """
    Accepts:
      - dict / dataclass envelope
      - CBOR bytes
      - hex string of CBOR bytes
    Returns a mutable dict.
    """
    obj = _coerce_mapping(raw_tx_template)
    if isinstance(obj, dict):
        return obj

    try:
        import cbor2  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "CBOR support missing. Install dependency: pip install cbor2\n"
            f"Details: {e}"
        ) from e

    raw_bytes = _coerce_cbor_input(obj)
    decoded = cbor2.loads(raw_bytes)
    if not isinstance(decoded, dict):
        raise TypeError("decoded tx envelope must be a CBOR map/dict")
    return decoded


def _encode_envelope(env: dict) -> bytes:
    try:
        import cbor2  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "CBOR support missing. Install dependency: pip install cbor2\n"
            f"Details: {e}"
        ) from e
    return cbor2.dumps(env)


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------
@app.command()
def build(
    from_addr: str = typer.Option(..., "--from", help="Sender address"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address"),
    value: float = typer.Option(0, "--value", help="Amount to transfer (in ANM)"),
    data: Optional[str] = typer.Option(None, "--data", help="Contract call data (hex, starts with 0x)"),
    gas: int = typer.Option(200000, "--gas", help="Gas limit"),
    gas_price: Optional[float] = typer.Option(None, "--gas-price", help="Gas price (gwei; 1 gwei = 1 base unit)"),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Transaction nonce (auto-fetched if omitted)"),
    chain_id: Optional[int] = typer.Option(None, "--chain-id", envvar="ANIMICA_CHAIN_ID", help="Chain ID (auto if omitted)"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Save tx JSON to file"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar="ANIMICA_RPC_URL", help="Override RPC URL"),
) -> None:
    try:
        url = _resolve_rpc_url(rpc_url)
        cfg = load_network_config()
        resolved_chain_id, _ = resolve_chain_id(url, chain_id, cfg.chain_id)

        if nonce is None:
            try:
                nonce_result = _request_rpc("state.getTransactionCount", [from_addr], url)
                nonce = int(nonce_result) if nonce_result else 0
            except Exception:
                nonce = 0

        tx_data = {
            "from": from_addr,
            "to": to_addr,
            "value": to_base_units(value) if value else 0,
            "data": data or "0x",
            "gas": gas,
            "gasPrice": int(Decimal(str(gas_price))) if gas_price is not None else 1,
            "nonce": nonce,
            "chainId": resolved_chain_id,
        }

        if output:
            output.write_text(json.dumps(tx_data, indent=2))
            typer.echo(f"✓ Transaction saved to {output}")
        else:
            typer.echo("Transaction (unsigned):")
            typer.echo(_pretty(tx_data))

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error building transaction: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def sign(
    tx_file: Path = typer.Option(..., "--file", "-f", help="Transaction JSON file"),
    key_id: Optional[str] = typer.Option(None, "--key", help="Key ID or wallet index (not implemented)"),
) -> None:
    typer.echo("Transaction signing subcommand is not implemented; use `animica tx send ...`.", err=True)
    raise typer.Exit(1)


@app.command()
def send(
    from_addr: str = typer.Option(..., "--from", help="Sender address or wallet label"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address"),
    value: float = typer.Option(..., "--value", help="Amount to transfer (in ANM)"),
    gas: Optional[int] = typer.Option(None, "--gas", help="Gas limit (auto if omitted)"),
    gas_price: Optional[float] = typer.Option(None, "--gas-price", help="Gas price in gwei (1 gwei = 1 base unit; auto if omitted)"),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Transaction nonce (auto-fetched if omitted)"),
    chain_id: Optional[int] = typer.Option(None, "--chain-id", envvar="ANIMICA_CHAIN_ID", help="Chain ID (auto if omitted)"),
    raw_out: Optional[Path] = typer.Option(None, "--raw-out", help="Write signing debug bundle (JSON) to the given file (dry-run only)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build and sign but do not broadcast"),
    wallet_file: Optional[Path] = typer.Option(None, "--wallet-file", envvar="ANIMICA_WALLETS_FILE", help="Override wallet store location"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar="ANIMICA_RPC_URL", help="Override RPC URL"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose signing debug"),
) -> None:
    try:
        if raw_out and not dry_run:
            typer.echo("Error: --raw-out is only available with --dry-run", err=True)
            raise typer.Exit(1)

        # PQ availability check
        from animica.cli.pq_utils import check_pq_signing_available, get_pq_missing_error_message  # type: ignore

        available, error_msg = check_pq_signing_available()
        if not available:
            typer.echo(get_pq_missing_error_message(), err=True)
            if error_msg:
                typer.echo(f"\nAdditional info: {error_msg}", err=True)
            raise typer.Exit(1)

        _warn_if_unsafe_pq_mode()

        # Resolve sender + wallet entry
        sender_address, wallet_entry = _resolve_sender(from_addr, wallet_file)
        dest_address = _resolve_destination(to_addr)

        url = _resolve_rpc_url(rpc_url)
        cfg = load_network_config()
        resolved_chain_id, chain_id_source = resolve_chain_id(url, chain_id, cfg.chain_id)

        if verbose:
            debug_chain_context(cfg.name, url, resolved_chain_id, chain_id_source)

        # Nonce
        if nonce is None:
            try:
                nonce_result = _request_rpc("state.getTransactionCount", [sender_address], url)
                nonce = int(nonce_result) if nonce_result else 0
            except Exception:
                nonce = 0

        # Gas / fee
        if gas is None:
            try:
                from omni_sdk.tx.build import suggest_gas_limit  # type: ignore

                gas = int(suggest_gas_limit("transfer"))
            except Exception:
                gas = 21000

        if gas_price is None:
            try:
                gas_price_result = _request_rpc("state.suggestGasPrice", [], url)
                gas_price = float(gas_price_result) if gas_price_result else 1.0
            except Exception:
                gas_price = 1.0

        value_units = to_base_units(value)
        max_fee = int(Decimal(str(gas_price)))

        # Build tx via omni_sdk (preserve envelope shape)
        try:
            from omni_sdk.tx.build import transfer  # type: ignore
            from omni_sdk.tx.signing import sign_transaction  # type: ignore
            from omni_sdk.wallet.signer import PQSigner  # type: ignore
        except Exception as e:
            typer.echo(f"Error: omni_sdk required but not available: {e}", err=True)
            raise typer.Exit(1)

        tx = transfer(
            from_addr=sender_address,
            to_addr=dest_address,
            amount=value_units,
            nonce=int(nonce),
            gas_limit=int(gas),
            max_fee=int(max_fee),
            chain_id=int(resolved_chain_id),
        )

        # signer from wallet entry (used to build an envelope template)
        signer = PQSigner.from_keypair(
            alg_name=wallet_entry.alg_name,
            secret_key=bytes.fromhex(wallet_entry.secret_key_hex.removeprefix("0x")),
            public_key=bytes.fromhex(wallet_entry.public_key_hex.removeprefix("0x")),
        )

        signed_tx = sign_transaction(tx, signer, resolved_chain_id)

        # Decode the envelope template into a dict (works if it's bytes, hex string, dict, dataclass)
        env = _decode_envelope(getattr(signed_tx, "raw_tx", signed_tx))

        # Canonical sign-bytes (node expects canonical CBOR(body))
        from animica.tx.signing import build_signable_tx_bytes  # type: ignore

        canonical_msg = build_signable_tx_bytes(env, chain_id=resolved_chain_id)

        # Create canonical PQ signature (Animica spec)
        from pq.py.sign import sign_detached  # type: ignore
        from pq.py.verify import verify_detached  # type: ignore

        sk = bytes.fromhex(wallet_entry.secret_key_hex.removeprefix("0x"))
        pk = bytes.fromhex(wallet_entry.public_key_hex.removeprefix("0x"))

        sig_env = sign_detached(
            canonical_msg,
            wallet_entry.alg_name,
            sk,
            domain="tx",
            chain_id=resolved_chain_id,
            prehash="sha3-512",
        )

        # Pre-flight verify locally
        ok = verify_detached(
            canonical_msg,
            sig_env,
            pk,
            chain_id=resolved_chain_id,
        )
        if not ok:
            typer.echo("=== Transaction Failed ===", err=True)
            typer.echo("Method:  tx.sendRawTransaction (pre-flight)", err=True)
            typer.echo("Message: Local PQ signature verification failed; not broadcasting", err=True)
            raise typer.Exit(1)

        # Patch envelope signature bytes/metadata (in-place)
        _patch_sig_in_envelope(
            env,
            sig_bytes=sig_env.sig,
            alg_id=sig_env.alg_id,
            domain=sig_env.domain,
            prehash=sig_env.prehash,
        )

        # Re-encode CBOR
        raw_tx = _encode_envelope(env)

        if verbose:
            typer.echo("", err=True)
            typer.echo("PQ SIGNATURE DEBUG (CANONICAL)", err=True)
            typer.echo(f"  algorithm: {sig_env.alg_name} (id={sig_env.alg_id})", err=True)
            typer.echo(f"  pubkey_len: {len(pk)} bytes", err=True)
            typer.echo(f"  sig_len: {len(sig_env.sig)} bytes", err=True)
            typer.echo(f"  message_len: {len(canonical_msg)} bytes", err=True)
            typer.echo(f"  message_prefix: {canonical_msg[:16].hex()}", err=True)
            typer.echo(f"  chain_id: {resolved_chain_id}", err=True)

            # Compare against omni_sdk sign-bytes (if provided)
            try:
                osb = getattr(signed_tx, "sign_bytes", None)
                if osb is not None:
                    osb_b = _coerce_cbor_input(osb) if not isinstance(osb, dict) else None
                    if isinstance(osb, (bytes, bytearray, memoryview)):
                        osb_b = bytes(osb)
                    elif isinstance(osb, str):
                        osb_b = _hex_to_bytes(osb) if osb.strip().startswith(("0x", "0X")) else osb.encode("utf-8")

                    if isinstance(osb_b, (bytes, bytearray)):
                        typer.echo("", err=True)
                        typer.echo("SIGN-BYTES COMPARISON", err=True)
                        typer.echo(f"  omni_sdk_len: {len(osb_b)}", err=True)
                        typer.echo(f"  canonical_len: {len(canonical_msg)}", err=True)
                        typer.echo(f"  omni_sdk_prefix: {bytes(osb_b)[:16].hex()}", err=True)
                        typer.echo(f"  canonical_prefix: {canonical_msg[:16].hex()}", err=True)
                        typer.echo(f"  equal: {bytes(osb_b) == canonical_msg}", err=True)
            except Exception:
                pass

            typer.echo("", err=True)

        # Dry-run or broadcast
        if dry_run:
            try:
                from omni_sdk.tx.encode import tx_hash_hex  # type: ignore
            except Exception:
                tx_hash_hex = None  # type: ignore

            raw_tx_hex = raw_tx.hex()
            raw_tx_prefixed = f"0x{raw_tx_hex}"

            value_decimal = Decimal(str(value))
            value_str = format(value_decimal, "f")

            typer.echo("=== Dry-Run Mode ===")
            typer.echo(f"From: {sender_address}")
            typer.echo(f"To:   {dest_address}")
            typer.echo(f"Value: {value_str} {COIN_SYMBOL} ({value_units} {UNIT_LABEL})")
            typer.echo(f"Gas Limit: {gas}")
            typer.echo(f"Max Fee:   {gas_price} gwei ({max_fee} {UNIT_LABEL})")
            typer.echo(f"Nonce:     {nonce}")
            typer.echo(f"Chain ID:  {resolved_chain_id}")

            if tx_hash_hex:
                try:
                    typer.echo(f"Tx Hash:   {tx_hash_hex(raw_tx)}")
                except Exception:
                    pass

            typer.echo(f"Raw Size:  {len(raw_tx)} bytes")
            typer.echo(f"RAW_TX={raw_tx_prefixed}")
            typer.echo("\n✓ Transaction built and signed (not broadcast)")

            if raw_out:
                artifact = {
                    "tx": {
                        "from": sender_address,
                        "to": dest_address,
                        "value": value_units,
                        "nonce": int(nonce),
                        "gasLimit": int(gas),
                        "maxFee": int(max_fee),
                        "chainId": int(resolved_chain_id),
                    },
                    "raw_tx_hex": raw_tx_prefixed,
                    "signing": {
                        "algorithm": {"id": sig_env.alg_id, "name": sig_env.alg_name},
                        "domain": sig_env.domain,
                        "prehash": sig_env.prehash,
                        "public_key_hex": pk.hex(),
                        "signature_hex": sig_env.sig.hex(),
                        "preimage_hex": canonical_msg.hex(),
                    },
                }
                raw_out.write_text(json.dumps(artifact, indent=2))
            return

        # Broadcast
        try:
            from omni_sdk.rpc.http import RpcClient  # type: ignore
            from omni_sdk.tx.send import submit_raw  # type: ignore

            rpc = RpcClient(url, timeout=30.0)
            tx_hash = submit_raw(rpc, raw_tx)

            typer.echo("=== Transaction Submitted ===")
            typer.echo(f"Tx Hash: {tx_hash}")
            typer.echo(f"From:    {sender_address}")
            typer.echo(f"To:      {dest_address}")
            typer.echo(f"Value:   {value} ANM")
            typer.echo("\n✓ Transaction broadcast successfully")

        except Exception as e:
            if e.__class__.__name__ == "RpcError":
                try:
                    typer.echo("=== Transaction Failed ===", err=True)
                    method = getattr(e, "method", None)
                    code = getattr(e, "code", None)
                    msg = getattr(e, "message", None)
                    data = getattr(e, "data", None)
                    if method:
                        typer.echo(f"Method:  {method}", err=True)
                    if code is not None:
                        typer.echo(f"Code:    {code}", err=True)
                    if msg:
                        typer.echo(f"Message: {msg}", err=True)
                    if data:
                        typer.echo(f"Data:    {data}", err=True)
                    raise typer.Exit(1)
                except typer.Exit:
                    raise
                except Exception:
                    pass

            typer.echo(f"Error broadcasting transaction: {e}", err=True)
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def simulate(
    tx_file: Path = typer.Option(..., "--file", "-f", help="Transaction JSON file"),
    from_addr: Optional[str] = typer.Option(None, "--from", help="Override sender for simulation"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar="ANIMICA_RPC_URL", help="Override RPC URL"),
) -> None:
    typer.echo("Simulation is not implemented yet.", err=True)
    raise typer.Exit(1)
