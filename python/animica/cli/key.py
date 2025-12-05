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
    _ensure_pq_available()
    _ensure_sdk_available()

    try:
        # Generate keypair using PQ module
        alg_obj = default_signature_alg()
        pk_bytes, sk_bytes = keygen_sig(alg_obj)

        # Derive address
        addr = from_pubkey(pk_bytes, alg_obj.alg_id)

        # Build output
        key_data = {
            "label": label or "",
            "algorithm": alg_obj.alg_id,
            "algorithm_name": (
                name_of(alg_obj.alg_id) if HAVE_PQ else f"0x{alg_obj.alg_id:04x}"
            ),
            "public_key_hex": pk_bytes.hex(),
            "secret_key_hex": sk_bytes.hex(),
            "address": addr,
        }

        if output:
            output_path = Path(output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(key_data, indent=2))
            output_path.chmod(0o600)
            typer.echo(f"✓ Key saved to {output_path}")
            typer.echo(f"  Address: {addr}")
            typer.echo(f"  Algorithm: {key_data['algorithm_name']}")
        else:
            typer.echo(json.dumps(key_data, indent=2))

    except Exception as e:
        typer.echo(f"Error generating key: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def show(
    key_id: str = typer.Argument(..., help="Key ID, file path, or address to lookup"),
) -> None:
    """Display key details (public key, address, algorithm)."""
    _ensure_sdk_available()

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
            addr = data.get("address", "unknown")
            alg = data.get("algorithm_name", data.get("algorithm", "?"))
            typer.echo(f"  {label:20} {addr:50} {alg}")
        except Exception as e:
            typer.echo(f"  {key_file.name:20} (error: {e})", err=True)
