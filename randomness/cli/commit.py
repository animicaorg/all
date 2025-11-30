"""
randomness.cli.commit
---------------------

Thin CLI for submitting a commit to the randomness service.

Usage:
  omni rand commit --salt 0xdeadbeef --payload 0x0123
  # Optionally specify the address (otherwise taken from env):
  omni rand commit --address anim1... --salt 0x.. --payload 0x..

Environment:
  OMNI_RPC_URL / ANIMICA_RPC_URL  : JSON-RPC endpoint (default: http://127.0.0.1:8545)
  OMNI_ADDRESS / ANIMICA_ADDRESS  : Default sender address if --address is omitted
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional, Sequence

# Optional deps guard
try:
    import requests  # type: ignore
    import typer  # type: ignore
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "This command requires optional dependencies.\n"
        "Install with: pip install typer[all] requests\n"
        f"Import error: {e}"
    )

_DEFAULT_RPC = (
    os.getenv("OMNI_RPC_URL") or os.getenv("ANIMICA_RPC_URL") or "http://127.0.0.1:8545"
)
_DEFAULT_ADDR = os.getenv("OMNI_ADDRESS") or os.getenv("ANIMICA_ADDRESS")

app = typer.Typer(
    name="omni-rand-commit",
    help="Submit a commitment for the current randomness round.",
    no_args_is_help=True,
    add_completion=False,
)


def _rpc_call(
    url: str, method: str, params: Optional[Sequence[Any]] = None, timeout: float = 10.0
) -> Dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params or [])}
    try:
        r = requests.post(url, json=body, timeout=timeout)
    except Exception as e:
        raise SystemExit(f"RPC POST failed: {e}")
    if r.status_code != 200:
        raise SystemExit(f"RPC error HTTP {r.status_code}: {r.text}")
    try:
        data = r.json()
    except Exception:
        raise SystemExit(f"RPC response not JSON: {r.text}")
    if "error" in data and data["error"]:
        raise SystemExit(f"RPC error: {json.dumps(data['error'], indent=2)}")
    return data.get("result")


def _is_hex(s: str) -> bool:
    return (
        s.startswith(("0x", "0X"))
        and all(c in "0123456789abcdefABCDEF" for c in s[2:])
        and (len(s) % 2 == 0)
    )


@app.command("commit")
def cmd_commit(
    salt: str = typer.Option(
        ..., "--salt", "-s", help="0x-hex salt used to build the commitment."
    ),
    payload: str = typer.Option(
        ..., "--payload", "-p", help="0x-hex payload being committed to."
    ),
    address: Optional[str] = typer.Option(
        None,
        "--address",
        "-a",
        help="Sender address (defaults to env OMNI_ADDRESS/ANIMICA_ADDRESS).",
    ),
    rpc: str = typer.Option(
        _DEFAULT_RPC, "--rpc", help=f"JSON-RPC endpoint (default: {_DEFAULT_RPC})"
    ),
) -> None:
    """
    Submit a commitment for the current open round.

    Server computes C = H(domain|address|salt|payload) and records it if commit window is open.
    """
    addr = address or _DEFAULT_ADDR
    if not addr:
        raise SystemExit(
            "Missing --address (and OMNI_ADDRESS/ANIMICA_ADDRESS not set)."
        )
    if not _is_hex(salt):
        raise SystemExit("Invalid --salt: must be even-length 0x-hex.")
    if not _is_hex(payload):
        raise SystemExit("Invalid --payload: must be even-length 0x-hex.")

    result = _rpc_call(
        rpc,
        "rand.commit",
        [{"address": addr, "salt": salt, "payload": payload}],
    )
    typer.echo(json.dumps(result, indent=2))


def main() -> None:  # pragma: no cover
    try:
        app(standalone_mode=False, prog_name="omni rand commit")
    except SystemExit as e:
        raise e
    except KeyboardInterrupt:
        typer.echo("", err=True)
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
