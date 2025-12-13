
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import click as _click
import httpx
import typer

from animica.config import load_network_config
from animica.cli.paths import ensure_file_dir, secure_file
from animica.coin import format_amount

try:
    from pq.py.address import address_from_pubkey, validate_address
    from pq.py.keygen import keygen_sig
    from pq.py.registry import default_signature_alg, name_of  # type: ignore
    HAVE_PQ = True
except Exception:
    HAVE_PQ = False

# Fallbacks when PQ package is not available
if not HAVE_PQ:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except Exception:
        Ed25519PrivateKey = None

    def default_signature_alg():
        class _Alg:
            alg_id = 0xFFFF
            name = "ed25519-fallback"
        return _Alg()

    def name_of(alg_id: int) -> str:  # pragma: no cover
        return "ed25519-fallback" if alg_id == 0xFFFF else f"0x{alg_id:04x}"


WALLET_FILE_ENV = "ANIMICA_WALLETS_FILE"
_RPC_ENV = "ANIMICA_RPC_URL"

app = typer.Typer(
    help=(
        "Wallet helper for creating, listing, and inspecting Animica addresses. "
        "For testnet funds use `animica faucet request <address>`; the wallet CLI"
        " does not request funds."
    )
)


@app.command("request")
def wallet_request_alias() -> None:
    """Guide users to the faucet when they try `animica wallet request`."""

    typer.echo(
        "Wallet funds are requested via `animica faucet request <address>`; "
        "the wallet command does not contact the faucet.",
        err=True,
    )
    raise typer.Exit(code=1)


@dataclass
class WalletEntry:
    label: str
    address: str
    alg_id: int
    alg_name: str
    public_key_hex: str
    secret_key_hex: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _get_default_wallet_path() -> Path:
    return Path.home() / ".animica" / "wallets.json"


def _wallet_file_path(wallet_file: Optional[Path]) -> Path:
    if wallet_file is not None:
        return Path(wallet_file)
    env_path = os.environ.get(WALLET_FILE_ENV)
    if env_path:
        return Path(env_path)
    return _get_default_wallet_path()


def _load_store(wallet_file: Path) -> Dict[str, Any]:
    if not wallet_file.exists():
        ensure_file_dir(wallet_file, sensitive=True)
        store = {"version": 1, "wallets": []}
        wallet_file.write_text(json.dumps(store, indent=2), encoding="utf-8")
        secure_file(wallet_file)
        return store
    data = json.loads(wallet_file.read_text(encoding="utf-8"))
    if "wallets" not in data:
        raise RuntimeError(f"Malformed wallet store at {wallet_file}")
    return data


def _save_store(wallet_file: Path, store: Dict[str, Any]) -> None:
    ensure_file_dir(wallet_file, sensitive=True)
    wallet_file.write_text(json.dumps(store, indent=2), encoding="utf-8")
    secure_file(wallet_file)


def _entry_from_dict(entry: Dict[str, Any]) -> WalletEntry:
    alg_id = int(entry.get("alg_id", default_signature_alg().alg_id))
    try:
        alg_name = entry.get("alg_name") or name_of(alg_id)
    except Exception:
        alg_name = entry.get("alg_name") or f"0x{alg_id:04x}"

    return WalletEntry(
        label=entry.get("label") or "",
        address=entry["address"],
        alg_id=alg_id,
        alg_name=alg_name,
        public_key_hex=entry["public_key_hex"],
        secret_key_hex=entry["secret_key_hex"],
        created_at=entry["created_at"],
    )


def _find_wallet(store: Dict[str, Any], *, identifier: str) -> WalletEntry:
    for entry in store.get("wallets", []):
        if (
            entry.get("address") == identifier
            or entry.get("label") == identifier
            or entry.get("public_key_hex") == identifier
        ):
            return _entry_from_dict(entry)
    typer.echo(f"Wallet not found: {identifier}", err=True)
    raise typer.Exit(code=1)


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    env_url = os.environ.get(_RPC_ENV)
    if env_url and env_url.strip():
        return env_url.strip()
    return load_network_config().rpc_url


def _fetch_balance(address: str, rpc_url: str) -> Optional[int]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "state.getBalance", "params": [address]}
    try:
        resp = httpx.post(rpc_url, json=payload, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return None
        result = data.get("result")
        if isinstance(result, str):
            if result.startswith("0x"):
                return int(result, 16)
            return int(result)
        if result is None:
            return None
        return int(result)
    except Exception:
        return None


def _generate_entry(label: str, *, allow_fallback: bool) -> WalletEntry:
    if allow_fallback:
        os.environ.setdefault("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
        os.environ.setdefault("ANIMICA_UNSAFE_PQ_FAKE", "1")

    alg_info = default_signature_alg()

    if HAVE_PQ:
        try:
            kp = keygen_sig(alg_info.alg_id)

            # HARD SAFETY CHECKS: refuse fake PQ wallets.
            public = kp.public_key
            secret = kp.secret_key

            if public == secret:
                raise RuntimeError("Refusing wallet: PQ keygen produced sk==pk (fake/broken)")
            if len(secret) <= len(public):
                raise RuntimeError(
                    f"Refusing wallet: suspicious PQ sizes pk={len(public)} sk={len(secret)}"
                )

            address = kp.address
            alg_name = kp.alg_name

        except NotImplementedError as e:
            if not allow_fallback:
                raise
            os.environ.setdefault("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
            os.environ.setdefault("ANIMICA_UNSAFE_PQ_FAKE", "1")
            from pq.py.algs import pure_python_fallbacks as pq_fallbacks  # type: ignore

            secret, public = pq_fallbacks.fallback_sig_keypair(alg_info.name)
            address = address_from_pubkey(public, alg_info.alg_id)
            alg_name = alg_info.name

    else:
        if Ed25519PrivateKey is None:
            raise RuntimeError("PQ not available and cryptography fallback not installed")

        from cryptography.hazmat.primitives import serialization

        sk = Ed25519PrivateKey.generate()
        pk = sk.public_key()
        public = pk.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        secret = sk.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        address = "anim1" + public.hex()
        alg_name = alg_info.name

    return WalletEntry(
        label=label,
        address=address,
        alg_id=alg_info.alg_id,
        alg_name=alg_name,
        public_key_hex=public.hex(),
        secret_key_hex=secret.hex(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


@app.callback()
def _configure(
    ctx: typer.Context,
    wallet_file: Optional[Path] = typer.Option(
        None,
        "--wallet-file",
        help="Override wallet store location (default: ~/.animica/wallets.json)",
        envvar=WALLET_FILE_ENV,
    ),
) -> None:
    if ctx.obj is None:
        ctx.obj = {}
    ctx.obj["wallet_file"] = wallet_file


def _current_wallet_file() -> Optional[Path]:
    try:
        ctx = typer.get_current_context(silent=True)
        if ctx and isinstance(getattr(ctx, "obj", None), dict):
            return ctx.obj.get("wallet_file")
    except Exception:
        pass
    try:
        ctx = _click.get_current_context(silent=True)
        if ctx and isinstance(getattr(ctx, "obj", None), dict):
            return ctx.obj.get("wallet_file")
    except Exception:
        pass
    return None


@app.command("create")
def create(
    label: str = typer.Option(..., "--label", help="Label for the new wallet"),
    allow_insecure_fallback: bool = typer.Option(
        False,
        "--allow-insecure-fallback",
        help="Use pure-Python PQ fallbacks when native libs are unavailable (dev/test only)",
    ),
) -> None:
    if not allow_insecure_fallback:
        from animica.cli.pq_utils import check_pq_signing_available, get_pq_missing_error_message

        ok, msg = check_pq_signing_available()
        if not ok:
            typer.echo(get_pq_missing_error_message(), err=True)
            if msg:
                typer.echo(f"\nAdditional info: {msg}", err=True)
            typer.echo("\nTo create a dev-only wallet, use --allow-insecure-fallback", err=True)
            raise typer.Exit(1)

    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)

    entry = _generate_entry(label, allow_fallback=allow_insecure_fallback)

    if HAVE_PQ:
        validate_address(entry.address, expect_hrp="anim")
    else:
        typer.echo("Warning: PQ not available; skipping address validation")

    if any(e.get("address") == entry.address for e in store.get("wallets", [])):
        typer.echo("Wallet already exists", err=True)
        raise typer.Exit(code=1)

    store.setdefault("wallets", []).append(entry.to_dict())
    _save_store(path, store)

    typer.echo("=== Wallet created ===")
    typer.echo(f"Label:   {entry.label}")
    typer.echo(f"Address: {entry.address}")
    typer.echo(f"Alg:     {entry.alg_name} (0x{entry.alg_id:04x})")
    typer.echo(f"Store:   {path}")


@app.command("list")
def list_wallets() -> None:  # noqa: A001
    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    wallets: List[Dict[str, Any]] = store.get("wallets", [])
    default_addr = store.get("default_address")

    typer.echo("Idx Default Label             Address                              Alg")
    typer.echo("--- ------- ----------------  -----------------------------------  ----------------")
    for idx, entry in enumerate(wallets):
        marker = "*" if entry.get("address") == default_addr else " "
        label = (entry.get("label") or "").ljust(16)
        address = entry.get("address") or ""
        alg_name = entry.get("alg_name") or ""
        typer.echo(f"{idx:>3} {marker} {label}  {address:<35}  {alg_name}")


@app.command()
def show(
    identifier: Optional[str] = typer.Argument(None, help="Address (bech32), label, or public key hex"),
    address: Optional[str] = typer.Option(None, "--address", help="(Deprecated) use positional argument"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="Animica JSON-RPC endpoint", envvar=_RPC_ENV),
) -> None:
    lookup_id = identifier or address
    if not lookup_id:
        typer.echo("Error: Missing wallet identifier", err=True)
        raise typer.Exit(code=1)

    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    entry = _find_wallet(store, identifier=lookup_id)

    balance = _fetch_balance(entry.address, _resolve_rpc_url(rpc_url))
    output = entry.to_dict()
    output["balance"] = balance
    if balance is not None:
        output["balance_formatted"] = format_amount(balance)
    typer.echo(json.dumps(output, indent=2))


@app.command()
def export(
    identifier: Optional[str] = typer.Argument(None, help="Address (bech32), label, or public key hex"),
    address: Optional[str] = typer.Option(None, "--address", help="(Deprecated) use positional argument"),
    out: Path = typer.Option(..., "--out", help="Destination JSON file"),
) -> None:
    lookup_id = identifier or address
    if not lookup_id:
        typer.echo("Error: Missing wallet identifier", err=True)
        raise typer.Exit(code=1)

    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    entry = _find_wallet(store, identifier=lookup_id)

    ensure_file_dir(out, sensitive=True)
    out.write_text(json.dumps(entry.to_dict(), indent=2), encoding="utf-8")
    secure_file(out)
    typer.echo(f"Exported to {out}")


@app.command(name="import")
def import_(  # noqa: A001
    file: Path = typer.Option(..., "--file", help="JSON file to import"),
    label: Optional[str] = typer.Option(None, "--label", help="Override label"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing address"),
) -> None:
    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)

    entry_data = json.loads(file.read_text(encoding="utf-8"))
    if label:
        entry_data["label"] = label
    if "address" not in entry_data:
        raise typer.BadParameter("Imported file missing address")

    validate_address(entry_data["address"], expect_hrp="anim")
    entry = _entry_from_dict(entry_data)

    existing = None
    for idx, candidate in enumerate(store.get("wallets", [])):
        if candidate.get("address") == entry.address:
            existing = idx
            break

    if existing is not None and not force:
        typer.echo("Wallet already exists; use --force to replace", err=True)
        raise typer.Exit(code=1)

    if existing is not None:
        store["wallets"][existing] = entry.to_dict()
    else:
        store.setdefault("wallets", []).append(entry.to_dict())

    _save_store(path, store)
    typer.echo(f"Imported wallet {entry.label or entry.address}")


@app.command(name="set-default")
def set_default(
    identifier: Optional[str] = typer.Argument(None, help="Address (bech32), label, or public key hex"),
    address: Optional[str] = typer.Option(None, "--address", help="(Deprecated) use positional argument"),
) -> None:
    lookup_id = identifier or address
    if not lookup_id:
        typer.echo("Error: Missing wallet identifier", err=True)
        raise typer.Exit(code=1)

    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    entry = _find_wallet(store, identifier=lookup_id)

    store["default_address"] = entry.address
    _save_store(path, store)
    typer.echo(f"Default wallet set to {entry.address}")


@app.command()
def env() -> None:  # noqa: A001
    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    default_address = store.get("default_address")
    if not default_address:
        typer.echo("No default wallet set; use `animica wallet set-default ...`", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"export ANIMICA_DEFAULT_ADDRESS={default_address}")


@app.command(name="new")
def new_alias(label: str = typer.Option(..., "--label")) -> None:
    create(label=label, allow_insecure_fallback=True)


if __name__ == "__main__":  # pragma: no cover
    app()
