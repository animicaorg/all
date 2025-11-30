"""
omni_sdk.cli.main
=================

`omni-sdk` — a practical command-line interface for Animica nodes and SDK
workflows. This entrypoint wires together built-in utility commands and, when
available, attaches richer subcommands from sibling modules:

- `deploy`      (if :mod:`omni_sdk.cli.deploy` is present)
- `call`        (if :mod:`omni_sdk.cli.call` is present)
- `subscribe`   (if :mod:`omni_sdk.cli.subscribe` is present)

The CLI honors environment overrides and explicit flags for RPC URL, chain ID,
and timeouts, and will *not* import heavier dependencies (like websockets or
Typer sub-apps) unless needed.

Examples
--------
    $ omni-sdk --rpc http://127.0.0.1:8545 version
    $ omni-sdk head
    $ omni-sdk params
    $ omni-sdk tx 0x1234...abcd
    $ omni-sdk receipt 0x1234...abcd
    $ omni-sdk ws-heads             # live newHeads (if websockets available)

Configuration
-------------
- RPC URL      : `--rpc` or env `OMNI_SDK_RPC_URL` (default: http://127.0.0.1:8545)
- Chain ID     : `--chain-id` or env `OMNI_CHAIN_ID` (default: 1)
- HTTP Timeout : `--timeout` or env `OMNI_SDK_HTTP_TIMEOUT` seconds (default: 10.0)

"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import typer

# --- SDK imports (kept light; optional modules are guarded) -------------------

# Version (soft dependency; graceful fallback)
try:
    from ..version import __version__ as SDK_VERSION  # type: ignore
except Exception:  # pragma: no cover
    SDK_VERSION = "0.0.0"

# HTTP JSON-RPC client
try:
    from ..rpc.http import RpcClient  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("omni_sdk.rpc.http.RpcClient is required by the CLI") from e


# --- Typer app and global context --------------------------------------------

app = typer.Typer(
    name="omni-sdk",
    help="Animica SDK CLI — query the node, inspect heads/txs, and more.",
    no_args_is_help=True,
    add_completion=False,
)

__all__ = ["app", "main", "run"]


@dataclass
class Ctx:
    rpc: str
    chain_id: int
    timeout: float


def _env_default(key: str, default: str) -> str:
    v = os.environ.get(key)
    return v if v is not None and v != "" else default


def _print_json(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=2, ensure_ascii=False))


@app.callback()
def _root(
    ctx: typer.Context,
    rpc: Optional[str] = typer.Option(
        None,
        "--rpc",
        help="Node HTTP JSON-RPC URL.",
        envvar="OMNI_SDK_RPC_URL",
    ),
    chain_id: Optional[int] = typer.Option(
        None,
        "--chain-id",
        help="Expected chain ID (for signing and safety checks).",
        envvar="OMNI_CHAIN_ID",
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help="HTTP timeout in seconds.",
        envvar="OMNI_SDK_HTTP_TIMEOUT",
    ),
) -> None:
    """
    Set effective configuration for this CLI process (and export to env so that
    lazily-imported subcommands share the same settings).
    """
    effective_rpc = rpc or _env_default("OMNI_SDK_RPC_URL", "http://127.0.0.1:8545")
    effective_chain = int(
        chain_id if chain_id is not None else int(_env_default("OMNI_CHAIN_ID", "1"))
    )
    effective_timeout = float(
        timeout
        if timeout is not None
        else float(_env_default("OMNI_SDK_HTTP_TIMEOUT", "10.0"))
    )

    # Persist to env for submodules that consult env vars
    os.environ["OMNI_SDK_RPC_URL"] = effective_rpc
    os.environ["OMNI_CHAIN_ID"] = str(effective_chain)
    os.environ["OMNI_SDK_HTTP_TIMEOUT"] = str(effective_timeout)

    ctx.obj = Ctx(
        rpc=effective_rpc, chain_id=effective_chain, timeout=effective_timeout
    )


def _client(ctx: typer.Context) -> RpcClient:
    c: Ctx = ctx.obj
    return RpcClient(c.rpc, timeout=c.timeout)


# --- Built-in lightweight commands -------------------------------------------


@app.command("version")
def version() -> None:
    """Print the SDK CLI version."""
    typer.echo(f"omni-sdk {SDK_VERSION}")


@app.command("env")
def env(ctx: typer.Context) -> None:
    """Show effective RPC URL, chain ID, and timeout."""
    c: Ctx = ctx.obj
    _print_json(
        {
            "rpc": c.rpc,
            "chain_id": c.chain_id,
            "timeout": c.timeout,
            "sdk_version": SDK_VERSION,
        }
    )


@app.command("head")
def head(ctx: typer.Context) -> None:
    """Fetch and print the current chain head."""
    res = _client(ctx).call("chain.getHead", [])
    _print_json(res)


@app.command("params")
def params(ctx: typer.Context) -> None:
    """Fetch and print the current chain parameters."""
    res = _client(ctx).call("chain.getParams", [])
    _print_json(res)


@app.command("tx")
def tx(
    ctx: typer.Context,
    tx_hash: str = typer.Argument(..., help="Transaction hash (0x...)"),
) -> None:
    """
    Look up a transaction by hash.
    """
    res = _client(ctx).call("tx.getTransactionByHash", [tx_hash])
    _print_json(res)


@app.command("receipt")
def receipt(
    ctx: typer.Context,
    tx_hash: str = typer.Argument(..., help="Transaction hash (0x...)"),
) -> None:
    """Fetch a transaction receipt by hash."""
    res = _client(ctx).call("tx.getTransactionReceipt", [tx_hash])
    _print_json(res)


@app.command("block")
def block(
    ctx: typer.Context,
    number: Optional[int] = typer.Option(
        None, "--number", "-n", help="Block number (decimal)."
    ),
    block_hash: Optional[str] = typer.Option(
        None, "--hash", "-h", help="Block hash (0x...)."
    ),
    include_txs: bool = typer.Option(
        False, "--txs", help="Include transactions in the response."
    ),
    include_receipts: bool = typer.Option(
        False, "--receipts", help="Include receipts (if available)."
    ),
) -> None:
    """
    Fetch a block by number or hash. One of --number or --hash is required.
    """
    client = _client(ctx)
    if (number is None) == (block_hash is None):
        raise typer.BadParameter("Provide exactly one of --number or --hash")
    if number is not None:
        res = client.call(
            "chain.getBlockByNumber", [number, include_txs, include_receipts]
        )
    else:
        res = client.call(
            "chain.getBlockByHash", [block_hash, include_txs, include_receipts]
        )
    _print_json(res)


@app.command("ws-heads")
def ws_heads(ctx: typer.Context) -> None:
    """
    Subscribe to `newHeads` via WebSocket and print updates.
    Requires `omni_sdk.rpc.ws` and the `websockets` dependency.
    """
    try:
        from ..rpc.ws import WsClient  # type: ignore
    except Exception as e:  # pragma: no cover
        raise typer.BadParameter(
            "WebSocket client not available. Install websockets and omni_sdk.rpc.ws."
        ) from e

    c: Ctx = ctx.obj
    ws_url = c.rpc.replace("http://", "ws://").replace("https://", "wss://")
    client = WsClient(ws_url, timeout=c.timeout)
    typer.echo(f"Connecting to {ws_url} … (Ctrl+C to exit)")
    try:
        with client:
            sub_id = client.subscribe("newHeads")
            last_print = 0.0
            for evt in client.events(sub_id):
                # Throttle to avoid flooding
                now = time.time()
                if now - last_print >= 0.05:
                    _print_json(evt)
                    last_print = now
    except KeyboardInterrupt:
        typer.echo("bye")


# --- Optional subcommand wiring (attached if module is present) ---------------


def _attach_optional_subapp(module: str, attr: str, name: str, help_text: str) -> None:
    """
    Try to import a Typer sub-application (`attr`) from `module` and mount it
    under `name`. If import fails, mount a placeholder command group that
    explains how to enable the subcommand.
    """
    try:
        mod = __import__(module, fromlist=[attr])
        sub_app = getattr(mod, attr)
        if not isinstance(sub_app, typer.Typer):  # pragma: no cover
            raise TypeError(f"{module}.{attr} is not a Typer app")
        app.add_typer(sub_app, name=name, help=help_text)
        return
    except Exception:
        pass  # fall through to stub

    stub = typer.Typer(no_args_is_help=True)

    @stub.callback()
    def _(_: typer.Context) -> None:
        pass  # group help only

    @stub.command("help")
    def _help() -> None:
        typer.echo(
            f"Subcommand '{name}' is not available. "
            f"Ensure the module '{module}' exists and is importable."
        )

    app.add_typer(stub, name=name, help=f"{help_text} (module not installed)")


# Attach known sub-apps if present
_attach_optional_subapp(
    "omni_sdk.cli.deploy", "app", "deploy", "Deploy contracts and packages"
)
_attach_optional_subapp(
    "omni_sdk.cli.call", "app", "call", "Call contract functions (read/write)"
)
_attach_optional_subapp(
    "omni_sdk.cli.subscribe", "app", "subscribe", "Subscribe to heads/events via WS"
)


# --- Entrypoints --------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """
    Run the CLI. Returns an integer exit code.
    """
    try:
        app(prog_name="omni-sdk", standalone_mode=False, args=argv)
        return 0
    except typer.Exit as e:  # normal exit
        return int(e.exit_code)
    except Exception as e:
        # Pretty fallback error
        typer.echo(f"error: {e}", err=True)
        return 1


def run(argv: Optional[list[str]] = None) -> int:
    """Alias for :func:`main`."""
    return main(argv)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
