"""
randomness.cli.verify_vdf
-------------------------

Verify a Wesolowski VDF proof JSON file against a node-advertised round.

The tool fetches the VDF parameters for the target round via JSON-RPC
(`rand.getRound`), compares them with the proof file, and runs the reference
verifier. It exits 0 on success, non-zero on failure.

Examples:
  # Verify proof.json against the *current* round:
  omni rand verify-vdf proof.json

  # Verify against a specific round id:
  omni rand verify-vdf proof.json --round 1234

  # Read from stdin:
  cat proof.json | omni rand verify-vdf -

Environment:
  OMNI_RPC_URL / ANIMICA_RPC_URL : JSON-RPC endpoint (default: http://127.0.0.1:8545)
"""

from __future__ import annotations

import json
import os
import sys
import time
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

# Reference verifier
wesolowski_verify = None
try:
    from randomness.vdf.verifier import verify as wesolowski_verify  # type: ignore
except Exception:
    # Fallback verifier (NOT SECURE): recompute y by T squarings and ignore pi.
    # Only intended for tiny devnet params when the reference verifier isn't available.
    def _slow_verify(N: int, x: int, y: int, pi: int, T: int) -> bool:  # noqa: ARG001
        acc = x % N
        for _ in range(int(T)):
            acc = (acc * acc) % N
        return acc == (y % N)

    wesolowski_verify = _slow_verify  # type: ignore

_DEFAULT_RPC = os.getenv("OMNI_RPC_URL") or os.getenv("ANIMICA_RPC_URL") or "http://127.0.0.1:8545"

app = typer.Typer(
    name="omni-rand-verify-vdf",
    help="Verify a VDF proof JSON against the node-advertised round parameters.",
    no_args_is_help=True,
    add_completion=False,
)

# -----------------------
# Helpers
# -----------------------

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


def _hex_to_int(h: Any) -> int:
    if isinstance(h, int):
        return h
    if not isinstance(h, str):
        raise ValueError("expected int or 0x-hex string")
    s = h.lower()
    if s.startswith("0x"):
        return int(s, 16)
    # allow raw decimal strings as a convenience
    return int(s, 10)


def _load_json(path: str) -> Dict[str, Any]:
    if path == "-":
        txt = sys.stdin.read()
    else:
        with open(path, "r", encoding="utf-8") as f:
            txt = f.read()
    try:
        obj = json.loads(txt)
    except Exception as e:
        raise SystemExit(f"Invalid JSON in proof file: {e}")
    if not isinstance(obj, dict):
        raise SystemExit("Top-level JSON must be an object.")
    return obj


def _extract_proof(obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accepts shapes like:
      {
        "round": 1234,
        "modulus": "0x...",
        "input": "0x...",
        "iterations": 100000,
        "y": "0x...",
        "pi": "0x..."
      }
    or with {"proof": {"y": "...", "pi": "..."}}.
    """
    proof = dict(obj)
    if "proof" in obj and isinstance(obj["proof"], dict):
        proof.update(obj["proof"])

    required = ["modulus", "input", "iterations", "y", "pi"]
    missing = [k for k in required if k not in proof]
    if missing:
        raise SystemExit(f"Proof JSON missing required fields: {', '.join(missing)}")

    try:
        N = _hex_to_int(proof["modulus"])
        X = _hex_to_int(proof["input"])
        T = int(proof["iterations"])
        Y = _hex_to_int(proof["y"])
        PI = _hex_to_int(proof["pi"])
    except Exception as e:
        raise SystemExit(f"Failed to parse proof fields: {e}")

    round_id = proof.get("round")
    if round_id is not None:
        try:
            round_id = int(round_id)
        except Exception:
            raise SystemExit("round must be an integer if provided.")

    return {
        "round": round_id,
        "N": N,
        "X": X,
        "T": T,
        "Y": Y,
        "PI": PI,
    }


def _fetch_round(rpc: str, round_id: Optional[int]) -> Dict[str, Any]:
    # Our node supports rand.getRound([roundId?]).
    params: Sequence[Any] = [round_id] if round_id is not None else []
    result = _rpc_call(rpc, "rand.getRound", params)
    if not isinstance(result, dict):
        raise SystemExit("rand.getRound returned unexpected shape.")
    vdf = result.get("vdf") or {}
    needed = [("modulus", vdf.get("modulus")), ("input", vdf.get("input")), ("iterations", vdf.get("iterations"))]
    miss = [k for k, v in needed if v is None]
    if miss:
        raise SystemExit("Node did not return VDF parameters from rand.getRound; missing: " + ", ".join(miss))
    return result


def _cmp_expected_vs_proof(expected: Dict[str, Any], proof: Dict[str, Any]) -> Tuple[bool, Dict[str, Tuple[Any, Any]]]:
    exp_N = _hex_to_int(expected["vdf"]["modulus"])
    exp_X = _hex_to_int(expected["vdf"]["input"])
    exp_T = int(expected["vdf"]["iterations"])

    mismatches: Dict[str, Tuple[Any, Any]] = {}
    if exp_N != proof["N"]:
        mismatches["modulus"] = (exp_N, proof["N"])
    if exp_X != proof["X"]:
        mismatches["input"] = (exp_X, proof["X"])
    if exp_T != proof["T"]:
        mismatches["iterations"] = (exp_T, proof["T"])
    return (len(mismatches) == 0), mismatches


# -----------------------
# CLI
# -----------------------

@app.command("verify-vdf")
def cmd_verify_vdf(
    proof_file: str = typer.Argument(..., help="Path to proof JSON (or '-' for stdin)"),
    rpc: str = typer.Option(_DEFAULT_RPC, "--rpc", help=f"JSON-RPC endpoint (default: {_DEFAULT_RPC})"),
    round_id: Optional[int] = typer.Option(None, "--round", "-r", help="Round id to verify against (default: current)"),
    force: bool = typer.Option(False, "--force", "-f", help="Verify even if parameters differ from node's round."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only set exit code; print minimal output."),
) -> None:
    """
    Verify a VDF proof file against the node-advertised VDF parameters for the given round.
    """
    # Load and parse proof JSON
    proof_json = _load_json(proof_file)
    proof = _extract_proof(proof_json)

    # Fetch expected round params
    expected = _fetch_round(rpc, round_id if round_id is not None else proof.get("round"))

    ok_params, mismatches = _cmp_expected_vs_proof(expected, proof)
    if not ok_params and not force:
        out = {
            "ok": False,
            "reason": "parameters_mismatch",
            "mismatches": {k: [int(a), int(b)] for k, (a, b) in mismatches.items()},
            "expected": expected.get("vdf"),
        }
        if quiet:
            typer.Exit(code=2)
        typer.echo(json.dumps(out, indent=2))
        raise typer.Exit(code=2)

    # Run verifier
    start = time.perf_counter()
    try:
        verified = bool(wesolowski_verify(int(proof["N"]), int(proof["X"]), int(proof["Y"]), int(proof["PI"]), int(proof["T"])))  # type: ignore[arg-type]
    except Exception as e:
        if quiet:
            typer.Exit(code=3)
        typer.echo(json.dumps({"ok": False, "reason": "verifier_error", "error": str(e)}, indent=2))
        raise typer.Exit(code=3)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    result_obj = {
        "ok": verified,
        "round": expected.get("round"),
        "phase": expected.get("phase"),
        "paramsMatch": ok_params,
        "mismatches": {k: [int(a), int(b)] for k, (a, b) in mismatches.items()} if not ok_params else {},
        "verifyMs": round(elapsed_ms, 3),
    }

    if quiet:
        raise typer.Exit(code=0 if verified else 1)

    typer.echo(json.dumps(result_obj, indent=2))
    raise typer.Exit(code=0 if verified else 1)


def main() -> None:  # pragma: no cover
    try:
        app(standalone_mode=False, prog_name="omni rand verify-vdf")
    except SystemExit as e:
        raise e
    except KeyboardInterrupt:
        typer.echo("", err=True)
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
