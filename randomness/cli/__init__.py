"""
randomness.cli
--------------

Small convenience CLI for interacting with the randomness service via JSON-RPC.

Commands (requires `typer` and `requests`):
  - params     : Show beacon/randomness parameters.
  - round      : Show the current round and window timings.
  - commit     : Submit a commit (addr/salt/payload).
  - reveal     : Submit a reveal for a given round.
  - beacon     : Get the finalized beacon for a round (or latest if omitted).
  - history    : List recent beacons.

Environment:
  OMNI_RPC_URL (or ANIMICA_RPC_URL) may be set to override the default RPC endpoint.

Example:
  python -m randomness.cli params
  python -m randomness.cli commit --address anim1... --salt 0xdead --payload 0xfeed
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional, Sequence

__all__ = ["app", "main"]

_DEFAULT_RPC = os.getenv("OMNI_RPC_URL") or os.getenv("ANIMICA_RPC_URL") or "http://127.0.0.1:8545"


def _require_deps() -> None:
    try:
        import typer  # noqa: F401
        import requests  # noqa: F401
    except Exception as e:  # pragma: no cover - import guard
        msg = (
            "This CLI requires optional dependencies.\n"
            "Install with: pip install typer[all] requests\n"
            f"Import error: {e}"
        )
        raise SystemExit(msg)


_require_deps()
import typer  # type: ignore  # noqa: E402
import requests  # type: ignore  # noqa: E402


def _rpc_call(url: str, method: str, params: Optional[Sequence[Any]] = None, timeout: float = 10.0) -> Dict[str, Any]:
    """
    Minimal JSON-RPC 2.0 helper.
    """
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": list(params or []),
    }
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


app = typer.Typer(
    name="omni-rand",
    help="Animica randomness CLI (commit→reveal→VDF→beacon).",
    no_args_is_help=True,
    add_completion=False,
)


def _opt_rpc() -> str:
    return typer.Option(_DEFAULT_RPC, "--rpc", help=f"JSON-RPC endpoint (default: {_DEFAULT_RPC})")  # type: ignore[return-value]


@app.command("params")
def cmd_params(rpc: str = _opt_rpc()) -> None:
    """Show beacon/randomness parameters."""
    res = _rpc_call(rpc, "rand.getParams")
    typer.echo(json.dumps(res, indent=2))


@app.command("round")
def cmd_round(rpc: str = _opt_rpc()) -> None:
    """Show current round info (ids and window timestamps)."""
    res = _rpc_call(rpc, "rand.getRound")
    typer.echo(json.dumps(res, indent=2))


@app.command("commit")
def cmd_commit(
    address: str = typer.Option(..., "--address", "-a", help="Sender address (bech32m anim1… or hex)."),
    salt: str = typer.Option(..., "--salt", "-s", help="0x-hex salt (commit randomness)."),
    payload: str = typer.Option(..., "--payload", "-p", help="0x-hex payload committed to."),
    rpc: str = _opt_rpc(),
) -> None:
    """
    Submit a commitment for the current open round.

    The server computes C = H(domain|address|salt|payload) and records it if the
    commit window is open.
    """
    res = _rpc_call(rpc, "rand.commit", [{"address": address, "salt": salt, "payload": payload}])
    typer.echo(json.dumps(res, indent=2))


@app.command("reveal")
def cmd_reveal(
    round_id: int = typer.Option(..., "--round", "-r", help="Round id to reveal for."),
    address: str = typer.Option(..., "--address", "-a", help="Sender address used at commit time."),
    salt: str = typer.Option(..., "--salt", "-s", help="0x-hex salt used at commit time."),
    payload: str = typer.Option(..., "--payload", "-p", help="0x-hex payload to reveal."),
    rpc: str = _opt_rpc(),
) -> None:
    """Reveal the (salt, payload) for a round."""
    res = _rpc_call(
        rpc,
        "rand.reveal",
        [{"round_id": round_id, "address": address, "salt": salt, "payload": payload}],
    )
    typer.echo(json.dumps(res, indent=2))


@app.command("beacon")
def cmd_beacon(
    round_id: Optional[int] = typer.Option(None, "--round", "-r", help="Round id (omit for latest)."),
    rpc: str = _opt_rpc(),
) -> None:
    """Get the finalized beacon for a round (or latest if omitted)."""
    params: Sequence[Any] = [] if round_id is None else [{"round_id": round_id}]
    res = _rpc_call(rpc, "rand.getBeacon", params)
    typer.echo(json.dumps(res, indent=2))


@app.command("history")
def cmd_history(
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=1000, help="Max number of records."),
    before_round: Optional[int] = typer.Option(None, "--before", help="Return entries before this round id."),
    rpc: str = _opt_rpc(),
) -> None:
    """List recent beacons (most-recent first by default)."""
    params = [{"limit": limit, "before_round": before_round}]
    res = _rpc_call(rpc, "rand.getHistory", params)
    typer.echo(json.dumps(res, indent=2))


def main(argv: Optional[Sequence[str]] = None) -> None:  # pragma: no cover - thin wrapper
    """Entry-point to run as `python -m randomness.cli`."""
    try:
        app(standalone_mode=False, prog_name="omni rand")
    except SystemExit as e:
        # Typer raises SystemExit for normal flow; re-raise for proper exit code in -m mode.
        raise e
    except KeyboardInterrupt:
        typer.echo("", err=True)
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
