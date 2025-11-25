"""
randomness.cli.prove_vdf
------------------------

Devnet helper to run the reference Wesolowski VDF prover for the *current* round
input fetched from the node, and optionally submit the proof back over JSON-RPC.

Examples:
  # Just compute and print the proof (JSON):
  omni rand prove-vdf

  # Compute and submit the proof:
  omni rand prove-vdf --submit

Environment:
  OMNI_RPC_URL / ANIMICA_RPC_URL  : JSON-RPC endpoint (default: http://127.0.0.1:8545)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional, Sequence, Tuple

# Optional deps guard
try:
    import typer  # type: ignore
    import requests  # type: ignore
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "This command requires optional dependencies.\n"
        "Install with: pip install typer[all] requests\n"
        f"Import error: {e}"
    )

# Import the reference prover from our tree
try:
    from randomness.vdf.wesolowski import prove as wesolowski_prove  # type: ignore
except Exception as e:  # pragma: no cover
    wesolowski_prove = None  # type: ignore

_DEFAULT_RPC = os.getenv("OMNI_RPC_URL") or os.getenv("ANIMICA_RPC_URL") or "http://127.0.0.1:8545"

app = typer.Typer(
    name="omni-rand-prove-vdf",
    help="Run the reference VDF prover for the current round input (devnet helper).",
    no_args_is_help=False,
    add_completion=False,
)


def _rpc_call(url: str, method: str, params: Optional[Sequence[Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
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


def _hex_to_int(h: str) -> int:
    if not isinstance(h, str) or not h.startswith(("0x", "0X")):
        raise ValueError("expected 0x-hex string")
    return int(h, 16)


def _int_to_hex(i: int) -> str:
    h = hex(i)[2:]
    if len(h) % 2:
        h = "0" + h
    return "0x" + h


def _compute_vdf(n_hex: str, x_hex: str, iterations: int) -> Tuple[str, str]:
    """
    Compute Wesolowski proof (y, pi) given modulus N, input X, and iteration count T.
    Returns hex strings (0x...).
    """
    N = _hex_to_int(n_hex)
    X = _hex_to_int(x_hex)

    if wesolowski_prove is None:
        # Fallback slow prover (O(T) squarings) with dummy proof pi.
        # Intended only for tiny devnet params.
        y = X % N
        for _ in range(iterations):
            y = (y * y) % N
        # Dummy 'pi' using the same y (NOT SECURE). Only for graceful fallback.
        pi = y
    else:
        y_int, pi_int = wesolowski_prove(N=N, x=X, T=iterations)  # type: ignore
        y, pi = y_int, pi_int

    return _int_to_hex(y), _int_to_hex(pi)


@app.command("prove-vdf")
def cmd_prove_vdf(
    rpc: str = typer.Option(_DEFAULT_RPC, "--rpc", help=f"JSON-RPC endpoint (default: {_DEFAULT_RPC})"),
    submit: bool = typer.Option(False, "--submit", "-s", help="Submit the computed proof via rand.submitVDF."),
) -> None:
    """
    Fetch the current round VDF input from the node, run the reference prover locally,
    print the result, and optionally submit it back to the node.
    """
    # Query current round. Expected shape (example):
    # {
    #   "round": 1234,
    #   "phase": "vdf",  # or similar
    #   "vdf": {
    #       "input": "0x...",
    #       "iterations": 100000,
    #       "modulus": "0x..."
    #   }
    # }
    round_info = _rpc_call(rpc, "rand.getRound", [])
    if not isinstance(round_info, dict):
        raise SystemExit("rand.getRound returned unexpected shape.")

    vdf_info = round_info.get("vdf") or {}
    n_hex = vdf_info.get("modulus")
    x_hex = vdf_info.get("input")
    iterations = vdf_info.get("iterations")

    missing = [name for name, val in [("modulus", n_hex), ("input", x_hex), ("iterations", iterations)] if val is None]
    if missing:
        raise SystemExit(
            "Node did not return VDF parameters from rand.getRound; missing: "
            + ", ".join(missing)
        )

    round_id = round_info.get("round")
    phase = round_info.get("phase") or vdf_info.get("phase")

    typer.echo(f"Round: {round_id}  Phase: {phase}")
    typer.echo("Running VDF prover… (this may take a while on large iteration counts)", err=True)

    y_hex, pi_hex = _compute_vdf(n_hex, x_hex, int(iterations))

    result_obj = {
        "round": round_id,
        "input": x_hex,
        "modulus": n_hex,
        "iterations": int(iterations),
        "y": y_hex,
        "pi": pi_hex,
    }

    if submit:
        # Best-effort: submit via rand.submitVDF. If the node uses a different method name,
        # it will return an error explaining the mismatch.
        submit_res = _rpc_call(rpc, "rand.submitVDF", [result_obj])
        typer.echo(json.dumps({"proof": result_obj, "submitResult": submit_res}, indent=2))
    else:
        typer.echo(json.dumps(result_obj, indent=2))


def main() -> None:  # pragma: no cover
    try:
        app(standalone_mode=False, prog_name="omni rand prove-vdf")
    except SystemExit as e:
        raise e
    except KeyboardInterrupt:
        typer.echo("", err=True)
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
