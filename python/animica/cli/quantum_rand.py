"""
animica quantum rand — consume verifiable quantum randomness.

Produce and verify lottery draws, dice, shuffles, ranges, weighted choices, and
attested random bytes from the quantum beacon. Every draw is a pure function of
(beacon, request_id, params) so anyone can `verify` it offline.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Optional

import typer
from rich.console import Console

console = Console()

rand_app = typer.Typer(
    name="rand",
    help="Verifiable quantum randomness: beacon, lottery/draw, dice, attested bytes, verify",
    no_args_is_help=True,
)


def _rpc(rpc_url: str, method: str, params: dict) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(rpc_url, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:  # nosec - operator URL
        out = json.loads(r.read().decode())
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out.get("result") or {}


def _beacon_bytes(beacon: Optional[str], round_id: int, rpc_url: Optional[str]) -> bytes:
    if beacon:
        return bytes.fromhex(beacon[2:] if beacon.startswith("0x") else beacon)
    if rpc_url:
        bc = _rpc(rpc_url, "rand.getQuantumBeacon", {"round_id": round_id})
        if not bc.get("available"):
            raise typer.BadParameter(f"node has no beacon for round {round_id}: {bc.get('reason')}")
        return bytes.fromhex(bc["value_hex"])
    raise typer.BadParameter("provide --beacon HEX (offline) or --round + --rpc-url (fetch from node)")


@rand_app.command("beacon")
def beacon(round_id: int = typer.Option(0, "--round"),
           rpc_url: str = typer.Option(..., "--rpc-url")):
    """Fetch the verifiable quantum beacon for a round from a node."""
    console.print_json(json.dumps(_rpc(rpc_url, "rand.getQuantumBeacon", {"round_id": round_id})))


@rand_app.command("random")
def random_bytes(n: int = typer.Option(32, "--bytes"),
                 attested: bool = typer.Option(True, "--attested/--no-attested"),
                 rpc_url: Optional[str] = typer.Option(None, "--rpc-url")):
    """Attested quantum random bytes (local QRNG-as-a-service, or via a node)."""
    if rpc_url:
        out = _rpc(rpc_url, "rand.quantumRandomBytes", {"n": n, "attested": attested})
    else:
        from rpc.methods.quantum import quantum_random_bytes
        out = quantum_random_bytes(n=n, attested=attested)
    console.print_json(json.dumps(out))


@rand_app.command("lottery")
def lottery(entries: str = typer.Option(..., "--entries", help="comma-separated entries"),
            k: int = typer.Option(1, "--k", help="number of winners"),
            request_id: str = typer.Option("lottery", "--request"),
            round_id: int = typer.Option(0, "--round"),
            beacon: Optional[str] = typer.Option(None, "--beacon", help="beacon hex (offline)"),
            rpc_url: Optional[str] = typer.Option(None, "--rpc-url")):
    """Draw k distinct winners from a list (verifiable)."""
    from randomness.qrng import public
    items = [e for e in entries.split(",") if e != ""]
    b = _beacon_bytes(beacon, round_id, rpc_url)
    res = public.lottery_draw(b, round_id, request_id, items, k)
    console.print_json(json.dumps(res))


@rand_app.command("draw")
def draw(kind: str = typer.Option("dice", "--kind", help="dice|coin|range|shuffle|choice|weighted|bytes"),
         request_id: str = typer.Option("draw", "--request"),
         round_id: int = typer.Option(0, "--round"),
         beacon: Optional[str] = typer.Option(None, "--beacon"),
         rpc_url: Optional[str] = typer.Option(None, "--rpc-url"),
         sides: int = typer.Option(6, "--sides"), count: int = typer.Option(1, "--count"),
         lo: int = typer.Option(0, "--lo"), hi: int = typer.Option(100, "--hi"),
         items: Optional[str] = typer.Option(None, "--items", help="comma-separated"),
         weights: Optional[str] = typer.Option(None, "--weights", help="comma-separated ints"),
         nbytes: int = typer.Option(32, "--nbytes")):
    """Generic verifiable draw of a given kind."""
    from randomness.qrng import public
    b = _beacon_bytes(beacon, round_id, rpc_url)
    p: dict = {}
    if kind == "dice":
        p = {"sides": sides, "count": count}
    elif kind == "coin":
        p = {"count": count}
    elif kind == "range":
        p = {"lo": lo, "hi": hi, "count": count}
    elif kind in ("shuffle", "choice"):
        p = {"items": (items or "").split(",")}
    elif kind == "weighted":
        p = {"items": (items or "").split(","), "weights": [int(x) for x in (weights or "").split(",") if x]}
    elif kind == "bytes":
        p = {"n": nbytes}
    else:
        raise typer.BadParameter(f"unknown kind: {kind}")
    console.print_json(json.dumps(public.compute(kind, b, round_id, request_id, p)))


@rand_app.command("verify")
def verify(result_file: str = typer.Argument(..., help="path to a draw-result JSON (or '-' for stdin)")):
    """Client-side verify: recompute a draw from its inputs and confirm the output."""
    import sys
    from randomness.qrng import public
    raw = sys.stdin.read() if result_file == "-" else open(result_file).read()
    res = json.loads(raw)
    ok = public.verify_result(res)
    console.print(f"[bold {'green' if ok else 'red'}]verified={ok}[/]")
    raise typer.Exit(0 if ok else 1)
