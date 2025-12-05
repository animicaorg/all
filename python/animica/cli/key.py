"""
animica.cli.key — Key management subcommands.

Implements:
  - animica key new          Generate new keypair
  - animica key show <id>    Display key details
  - animica key list         List all keys
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer

try:
    from pq.py.keygen import keygen_sig
    from pq.py.registry import default_signature_alg, name_of

    HAVE_PQ = True
except Exception:
    HAVE_PQ = False

# Fallbacks when PQ not available
if not HAVE_PQ:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import \
            Ed25519PrivateKey
    except Exception:
        Ed25519PrivateKey = None
        serialization = None

    def default_signature_alg():
        class _Alg:
            alg_id = 0xFFFF
            name = "ed25519-fallback"

        return _Alg()

    def name_of(alg_id: int) -> str:
        return "ed25519-fallback" if alg_id == 0xFFFF else f"0x{alg_id:04x}"


try:
    from omni_sdk.address import from_pubkey, validate

    HAVE_SDK = True
except Exception:
    HAVE_SDK = False

app = typer.Typer(help="Key management (generate, show, list)")

DEFAULT_KEY_DIR = Path.home() / ".animica" / "keys"


def _ensure_pq_available() -> None:
    if not HAVE_PQ:
        typer.echo(
            "Error: PQ cryptography module required. "
            "Ensure 'pq' package is installed.",
            err=True,
        )
        raise typer.Exit(1)


def _ensure_sdk_available() -> None:
    if not HAVE_SDK:
        typer.echo(
            "Error: omni_sdk.address module required. "
            "Ensure 'omni_sdk' is installed.",
            err=True,
        )
        raise typer.Exit(1)


@app.command()
def new(
    label: Optional[str] = typer.Option(
        None,
        "--label",
        help="Optional label for this key",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Save key to file (default: prints to stdout)",
    ),
    alg: Optional[str] = typer.Option(
        None,
        "--alg",
        help="Signature algorithm (default: Dilithium3)",
    ),
) -> None:
    """Generate a new keypair using the default signature algorithm (Dilithium3)."""
    # Generate keypair
    alg_obj = default_signature_alg()
    try:
        if HAVE_PQ:
            pk_bytes, sk_bytes = keygen_sig(alg_obj)
            addr = None
            if HAVE_SDK:
                try:
                    addr = from_pubkey(pk_bytes, alg_obj.alg_id)
                except Exception:
                    addr = None
        else:
            if Ed25519PrivateKey is None or serialization is None:
                typer.echo(
                    "Error: PQ not available and cryptography fallback not installed.",
                    err=True,
                )
                raise typer.Exit(1)
            sk = Ed25519PrivateKey.generate()
            pk = sk.public_key()
            pk_bytes = pk.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            sk_bytes = sk.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            addr = None
            if HAVE_SDK:
                try:
                    addr = from_pubkey(pk_bytes, alg_obj.alg_id)
                except Exception:
                    addr = None

        key_data = {
            "label": label or "",
            "algorithm": alg_obj.alg_id,
            "algorithm_name": name_of(alg_obj.alg_id),
            "public_key_hex": pk_bytes.hex(),
            "secret_key_hex": sk_bytes.hex(),
            "address": addr,
        }

        if output:
            output_path = Path(output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(key_data, indent=2))
            try:
                output_path.chmod(0o600)
            except Exception:
                pass
            typer.echo(f"✓ Key saved to {output_path}")
            if key_data.get("address"):
                typer.echo(f"  Address: {key_data['address']}")
            typer.echo(f"  Algorithm: {key_data['algorithm_name']}")
        else:
            typer.echo(json.dumps(key_data, indent=2))

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error generating key: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def show(
    key_id: str = typer.Argument(..., help="Key ID, file path, or address to lookup"),
) -> None:
    """Display key details (public key, address, algorithm)."""
    # Don't require omni_sdk.address to display saved key files; only use it
    # for address validation when available.

    try:
        key_path = Path(key_id)

        # Try to load from file
        if key_path.exists():
            key_data = json.loads(key_path.read_text())
            typer.echo(f"Key: {key_data.get('label', 'unlabeled')}")
            typer.echo(f"Address: {key_data['address']}")
            typer.echo(
                f"Algorithm: {key_data.get('algorithm_name', key_data['algorithm'])}"
            )
            typer.echo(f"Public key: {key_data['public_key_hex']}")
            if key_data.get("secret_key_hex"):
                typer.echo("⚠️  Secret key present in file (keep this file secure!)")
            return

        # Try as address
        if key_id.startswith("anim1"):
            try:
                validate(key_id)
                typer.echo(f"Valid address: {key_id}")
                # Could query chain for account state here
                return
            except Exception:
                pass

        typer.echo(f"Key not found: {key_id}", err=True)
        raise typer.Exit(1)

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command("list")
def list_keys(
    key_dir: Optional[Path] = typer.Option(
        None,
        "--dir",
        help="Key directory (default: ~/.animica/keys)",
    ),
) -> None:
    """List all keys in the keystore directory."""
    dir_path = key_dir or DEFAULT_KEY_DIR

    if not dir_path.exists():
        typer.echo(f"No keys directory found at {dir_path}")
        return

    keys = list(dir_path.glob("*.json"))

    if not keys:
        typer.echo(f"No keys found in {dir_path}")
        return

    typer.echo(f"Keys in {dir_path}:")
    for key_file in sorted(keys):
        try:
            data = json.loads(key_file.read_text())
            label = data.get("label") or key_file.stem
            addr = data.get("address") or ""
            alg = str(data.get("algorithm_name") or data.get("algorithm") or "?")
            typer.echo(f"  {label:20} {addr:50} {alg}")
        except Exception as e:
            typer.echo(f"  {key_file.name:20} (error: {e})", err=True)
