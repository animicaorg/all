"""Developer wallet management CLI for Animica.

This tool is intended for **local development only**. Keys are stored in a
plain JSON file and addresses are derived using a simplified devnet-friendly
scheme (keccak256 of the uncompressed public key, last 20 bytes). Do **not**
use this for production funds.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha3_256
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

DEFAULT_WALLET_PATH = Path.home() / ".animica" / "wallets.json"
WALLET_FILE_ENV = "ANIMICA_WALLETS_FILE"

app = typer.Typer(help="Developer wallet helper for Animica (devnet only).")


@dataclass
class WalletEntry:
    label: str
    address: str
    public_key_hex: str
    private_key_hex: str
    created_at: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "label": self.label,
            "address": self.address,
            "public_key_hex": self.public_key_hex,
            "private_key_hex": self.private_key_hex,
            "created_at": self.created_at,
        }


# -- minimal secp256k1 helpers (adapted from animica.wallet_cli) ----------------
_SECP256K1_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_SECP256K1_GX = 55066263022277343669578718895168534326250603453777594175500187360389116729240
_SECP256K1_GY = 32670510020758816978083085130507043184471273380659243275938904335757337482424


def _inverse_mod(k: int, p: int) -> int:
    return pow(k, -1, p)


def _is_on_curve(point: tuple[int, int] | None) -> bool:
    if point is None:
        return True
    x, y = point
    return (y * y - (x * x * x + 7)) % _SECP256K1_P == 0


def _point_add(p1: tuple[int, int] | None, p2: tuple[int, int] | None) -> tuple[int, int] | None:
    if p1 is None:
        return p2
    if p2 is None:
        return p1

    x1, y1 = p1
    x2, y2 = p2

    if x1 == x2 and y1 != y2:
        return None

    if x1 == x2:
        m = (3 * x1 * x1) * _inverse_mod(2 * y1, _SECP256K1_P)
    else:
        m = (y1 - y2) * _inverse_mod(x1 - x2, _SECP256K1_P)

    m %= _SECP256K1_P
    x3 = (m * m - x1 - x2) % _SECP256K1_P
    y3 = (m * (x1 - x3) - y1) % _SECP256K1_P
    return x3, y3


def _scalar_multiply(k: int, point: tuple[int, int] | None) -> tuple[int, int] | None:
    result = None
    addend = point
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def _generate_keypair() -> tuple[bytes, bytes]:
    private_int = secrets.randbelow(_SECP256K1_N - 1) + 1
    public_point = _scalar_multiply(private_int, (_SECP256K1_GX, _SECP256K1_GY))
    if public_point is None or not _is_on_curve(public_point):
        raise RuntimeError("Failed to generate secp256k1 keypair")
    x, y = public_point
    public_uncompressed = b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")
    return private_int.to_bytes(32, "big"), public_uncompressed


def _derive_address(public_key: bytes) -> str:
    digest = sha3_256(public_key).digest()
    return "0x" + digest[-20:].hex()


def _wallet_file_path(wallet_file: Optional[Path]) -> Path:
    if wallet_file is not None:
        return Path(wallet_file)
    return Path(os.environ.get(WALLET_FILE_ENV, DEFAULT_WALLET_PATH))


def _load_store(wallet_file: Path) -> Dict[str, Any]:
    if not wallet_file.exists():
        wallet_file.parent.mkdir(parents=True, exist_ok=True)
        store = {"version": 1, "wallets": []}
        wallet_file.write_text(json.dumps(store, indent=2), encoding="utf-8")
        return store
    data = json.loads(wallet_file.read_text(encoding="utf-8"))
    if "wallets" not in data:
        raise RuntimeError(f"Malformed wallet store at {wallet_file}")
    return data


def _save_store(wallet_file: Path, store: Dict[str, Any]) -> None:
    wallet_file.parent.mkdir(parents=True, exist_ok=True)
    wallet_file.write_text(json.dumps(store, indent=2), encoding="utf-8")


def _find_wallet(store: Dict[str, Any], *, label: Optional[str], address: Optional[str]) -> Dict[str, Any]:
    for entry in store.get("wallets", []):
        if label and entry.get("label") == label:
            return entry
        if address and entry.get("address") == address:
            return entry
    typer.echo("Wallet not found", err=True)
    raise typer.Exit(code=1)


def create_wallet(label: str, wallet_file: Optional[Path] = None) -> WalletEntry:
    path = _wallet_file_path(wallet_file)
    store = _load_store(path)
    private_key, public_key = _generate_keypair()
    address = _derive_address(public_key)
    entry = WalletEntry(
        label=label,
        address=address,
        public_key_hex=public_key.hex(),
        private_key_hex=private_key.hex(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    store.setdefault("wallets", []).append(entry.to_dict())
    _save_store(path, store)
    return entry


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
    ctx.obj = {"wallet_file": wallet_file}


def _current_wallet_file() -> Optional[Path]:
    ctx = typer.get_current_context(silent=True)
    if ctx and ctx.obj:
        return ctx.obj.get("wallet_file")
    return None


@app.command()
def new(label: str = typer.Option(..., "--label", help="Label for the new wallet")) -> None:
    """Generate a new development wallet."""
    ctx_wallet_file = _current_wallet_file()
    entry = create_wallet(label, ctx_wallet_file)
    target_path = _wallet_file_path(ctx_wallet_file)
    typer.echo(f"Label:   {entry.label}")
    typer.echo(f"Address: {entry.address}")
    typer.echo(f"Public:  {entry.public_key_hex}")
    typer.echo(f"Stored in: {target_path}")


@app.command()
def list() -> None:  # noqa: A001
    """List known wallets."""
    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    wallets: List[Dict[str, Any]] = store.get("wallets", [])
    if not wallets:
        typer.echo("No wallets found")
        return
    typer.echo("#  Label            Address                        Created")
    typer.echo("-- ---------------- ------------------------------ ---------------------------")
    for idx, entry in enumerate(wallets):
        label = (entry.get("label") or "").ljust(16)
        address = entry.get("address") or ""
        created = entry.get("created_at") or ""
        typer.echo(f"{idx:<2} {label} {address:<30} {created}")


@app.command()
def show(
    label: Optional[str] = typer.Option(None, "--label", help="Lookup by label"),
    address: Optional[str] = typer.Option(None, "--address", help="Lookup by address"),
) -> None:
    """Display wallet details."""
    if not label and not address:
        raise typer.BadParameter("Provide --label or --address")
    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    entry = _find_wallet(store, label=label, address=address)
    typer.echo(json.dumps(entry, indent=2))


@app.command()
def export(
    address: str = typer.Option(..., "--address", help="Address to export"),
    out: Path = typer.Option(..., "--out", help="Destination JSON file"),
) -> None:
    """Export a wallet's private key and metadata."""
    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    entry = _find_wallet(store, label=None, address=address)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    typer.echo(f"Exported to {out}")


@app.command(name="import")
def import_(
    file: Path = typer.Option(..., "--file", help="JSON file to import"),
    label: Optional[str] = typer.Option(None, "--label", help="Override label"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing address"),
) -> None:
    """Import a wallet JSON file."""
    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    entry = json.loads(file.read_text(encoding="utf-8"))
    address = entry.get("address")
    if not address:
        raise typer.BadParameter("Imported file missing address")
    if label:
        entry["label"] = label
    existing = None
    for idx, candidate in enumerate(store.get("wallets", [])):
        if candidate.get("address") == address:
            existing = idx
            break
    if existing is not None and not force:
        typer.echo("Wallet already exists; use --force to replace", err=True)
        raise typer.Exit(code=1)
    if existing is not None:
        store["wallets"][existing] = entry
    else:
        store.setdefault("wallets", []).append(entry)
    _save_store(path, store)
    typer.echo(f"Imported wallet {entry.get('label') or address}")


@app.command()
def default(address: str = typer.Option(..., "--address", help="Address to mark as default")) -> None:
    """Mark a wallet as the default for other commands."""
    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    _find_wallet(store, label=None, address=address)
    store["default_address"] = address
    _save_store(path, store)
    typer.echo(f"Default wallet set to {address}")


@app.command()
def env() -> None:  # noqa: A001
    """Emit shell exports for the default wallet."""
    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    default_address = store.get("default_address")
    if not default_address:
        typer.echo("No default wallet set; use `animica-wallet default --address ...`", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"export ANIMICA_DEFAULT_ADDRESS={default_address}")


if __name__ == "__main__":  # pragma: no cover
    app()
