"""
randomness.cli.get_beacon
-------------------------

Fetch and print the latest beacon and its light proof via JSON-RPC.

Examples:
  # Latest beacon (pretty JSON):
  omni rand get-beacon

  # Specific round:
  omni rand get-beacon --round 1234

  # Only print the beacon output (hex):
  omni rand get-beacon --output-only

  # Only print the light proof object:
  omni rand get-beacon --light-only

Environment:
  OMNI_RPC_URL / ANIMICA_RPC_URL : JSON-RPC endpoint (default: http://127.0.0.1:8545)
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

app = typer.Typer(
    name="omni-rand-get-beacon",
    help="Print the latest randomness beacon and its light proof.",
    no_args_is_help=False,
    add_completion=False,
)

# -----------------------
# Helpers
# -----------------------


def _rpc_call(
    url: str, method: str, params: Optional[Sequence[Any]] = None, timeout: float = 30.0
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
    result = data.get("result")
    if result is None:
        raise SystemExit("RPC returned no result.")
    if not isinstance(result, dict):
        # be liberal: some nodes might return a bare beacon object; normalize to dict
        return {"beacon": result}
    return result


def _normalize(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize possible shapes to a stable one:
      {
        "round": <int>,
        "beacon": { "output": "0x...", "prevOutput": "0x...", "timestamp": <int>, "vdf": {...} },
        "lightProof": { ... }
      }
    """
    round_id = result.get("round") or result.get("id") or result.get("roundId")
    beacon = result.get("beacon", {})
    if not beacon:
        # Some impls might flatten the fields
        beacon = {
            "output": result.get("output"),
            "prevOutput": result.get("prevOutput") or result.get("previous"),
            "timestamp": result.get("timestamp") or result.get("time"),
            "vdf": result.get("vdf")
            or {
                "modulus": result.get("modulus"),
                "input": result.get("input"),
                "iterations": result.get("iterations"),
                "y": result.get("y"),
                "pi": result.get("pi"),
            },
        }
    light = result.get("lightProof") or result.get("light") or {}
    return {"round": round_id, "beacon": beacon, "lightProof": light}


# -----------------------
# CLI
# -----------------------


@app.command("get-beacon")
def cmd_get_beacon(
    rpc: str = typer.Option(
        _DEFAULT_RPC, "--rpc", help=f"JSON-RPC endpoint (default: {_DEFAULT_RPC})"
    ),
    round_id: Optional[int] = typer.Option(
        None, "--round", "-r", help="Round id to fetch (default: latest)"
    ),
    light_only: bool = typer.Option(
        False, "--light-only", help="Print only the light proof JSON."
    ),
    output_only: bool = typer.Option(
        False, "--output-only", help="Print only the beacon output (hex)."
    ),
    raw: bool = typer.Option(
        False, "--raw", help="Print raw RPC result without normalization."
    ),
    out: Optional[str] = typer.Option(
        None, "--out", help="Write the (normalized) JSON to a file."
    ),
    pretty: bool = typer.Option(
        True, "--pretty/--no-pretty", help="Pretty-print JSON output."
    ),
) -> None:
    """
    Fetch latest (or specific) beacon and print it alongside the compact light proof.
    """
    params: Sequence[Any] = [round_id] if round_id is not None else []
    # Our node supports rand.getBeacon([roundId?]); with no params returns the latest.
    result = _rpc_call(rpc, "rand.getBeacon", params)

    if raw and not (light_only or output_only):
        text = json.dumps(result, indent=2 if pretty else None)
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            typer.echo(text)
        raise typer.Exit(0)

    normalized = _normalize(result)

    if output_only:
        output_hex = (normalized.get("beacon") or {}).get("output")
        if output_hex is None:
            raise SystemExit("Beacon output not found in RPC response.")
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write(
                    str(output_hex)
                    + ("\n" if not str(output_hex).endswith("\n") else "")
                )
        else:
            typer.echo(output_hex)
        raise typer.Exit(0)

    if light_only:
        text = json.dumps(
            normalized.get("lightProof", {}), indent=2 if pretty else None
        )
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
        else:
            typer.echo(text)
        raise typer.Exit(0)

    # Default: print normalized beacon + light proof
    text = json.dumps(normalized, indent=2 if pretty else None)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        typer.echo(text)
    raise typer.Exit(0)


def main() -> None:  # pragma: no cover
    try:
        app(standalone_mode=False, prog_name="omni rand get-beacon")
    except SystemExit as e:
        raise e
    except KeyboardInterrupt:
        typer.echo("", err=True)
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
