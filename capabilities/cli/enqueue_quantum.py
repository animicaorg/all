"""
capabilities.cli.enqueue_quantum
================================

Dev helper to enqueue a **Quantum** job into the Animica capabilities stack.

It tries several backends (in this order), using whichever is available
in your current checkout/install:

1) capabilities.host.compute.quantum_enqueue(...)
2) capabilities.adapters.aicf.enqueue_quantum(...)
3) capabilities.jobs.queue.JobQueue.enqueue_quantum(...)

Usage:
    python -m capabilities.cli enqueue-quantum --circuit-file bell_pair.json --shots 256
    python -m capabilities.cli enqueue-quantum --circuit '{"gates":[...],"qubits":2}' --shots 512 \
        --model qpu-sim --caller anim1xyz...

Options:
- You can force a backend with --backend host|aicf|queue
- For the 'queue' backend you can set --queue-db (default: ./capabilities_jobs.db)
- --json prints a machine-readable receipt line

This module exposes `register(app)` for dynamic discovery by
capabilities/cli/__init__.py.
"""

from __future__ import annotations

import json
import os
import sys
import inspect
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import typer  # type: ignore
except Exception as e:  # pragma: no cover
    raise ImportError("Typer is required. Install with: pip install typer[all]") from e


app = typer.Typer(
    name="enqueue-quantum",
    help="Enqueue a Quantum job (dev helper). Tries host/aicf/queue backends.",
    add_completion=False,
)

COMMAND_NAME = "enqueue-quantum"  # allows mount by name if register() not used


def _load_circuit(circuit: Optional[str], circuit_file: Optional[Path]) -> Dict[str, Any]:
    if circuit_file is not None:
        try:
            return json.loads(circuit_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise typer.BadParameter(f"Failed to read/parse --circuit-file: {e}")
    if circuit is None:
        raise typer.BadParameter("Provide either --circuit or --circuit-file")
    try:
        return json.loads(circuit)
    except Exception as e:
        raise typer.BadParameter(f"--circuit is not valid JSON: {e}")


def _call_with_supported_kwargs(fn, **kwargs):
    """
    Introspect `fn` and call it with only the kwargs it accepts.
    This lets us tolerate small signature differences across backends.
    """
    try:
        sig = inspect.signature(fn)
        accepted = {
            k: v
            for k, v in kwargs.items()
            if k in sig.parameters or any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
        }
        return fn(**accepted)
    except Exception:
        # Best-effort fallback without introspection
        return fn(**kwargs)  # type: ignore


def _pretty_print(obj: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        sys.stdout.write(json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n")
        return
    # human-ish
    txt = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)
    typer.echo(txt)


def _enqueue_via_host(
    circuit: Dict[str, Any],
    shots: int,
    model: Optional[str],
    caller: Optional[str],
    trap_ratio: Optional[float],
    seed: Optional[int],
) -> Dict[str, Any]:
    """
    capabilities.host.compute.quantum_enqueue(...)
    """
    from importlib import import_module

    compute = import_module("capabilities.host.compute")
    fn = getattr(compute, "quantum_enqueue", None)
    if not callable(fn):
        raise RuntimeError("capabilities.host.compute.quantum_enqueue not available")
    receipt = _call_with_supported_kwargs(
        fn,
        circuit=circuit,
        shots=shots,
        model=model,
        caller=caller,
        trap_ratio=trap_ratio,
        seed=seed,
    )
    # Normalize to dict
    if hasattr(receipt, "__dict__"):
        return dict(receipt.__dict__)  # type: ignore
    if isinstance(receipt, dict):
        return receipt
    return {"ok": True, "receipt": str(receipt)}


def _enqueue_via_aicf(
    circuit: Dict[str, Any],
    shots: int,
    model: Optional[str],
    caller: Optional[str],
    trap_ratio: Optional[float],
    seed: Optional[int],
) -> Dict[str, Any]:
    """
    capabilities.adapters.aicf.enqueue_quantum(...)
    """
    from importlib import import_module

    aicf = import_module("capabilities.adapters.aicf")
    fn = getattr(aicf, "enqueue_quantum", None)
    if not callable(fn):
        raise RuntimeError("capabilities.adapters.aicf.enqueue_quantum not available")
    receipt = _call_with_supported_kwargs(
        fn,
        circuit=circuit,
        shots=shots,
        model=model,
        caller=caller,
        trap_ratio=trap_ratio,
        seed=seed,
    )
    if hasattr(receipt, "__dict__"):
        return dict(receipt.__dict__)  # type: ignore
    if isinstance(receipt, dict):
        return receipt
    return {"ok": True, "receipt": str(receipt)}


def _enqueue_via_queue(
    circuit: Dict[str, Any],
    shots: int,
    model: Optional[str],
    caller: Optional[str],
    trap_ratio: Optional[float],
    seed: Optional[int],
    db_path: Path,
) -> Dict[str, Any]:
    """
    capabilities.jobs.queue.JobQueue.enqueue_quantum(...)
    """
    from importlib import import_module

    queue_mod = import_module("capabilities.jobs.queue")
    # Support either a class `JobQueue` or functions `open_queue()/enqueue_quantum()`
    JobQueue = getattr(queue_mod, "JobQueue", None)
    if JobQueue is not None:
        q = JobQueue(str(db_path))
        receipt = _call_with_supported_kwargs(
            q.enqueue_quantum,  # type: ignore[attr-defined]
            circuit=circuit,
            shots=shots,
            model=model,
            caller=caller,
            trap_ratio=trap_ratio,
            seed=seed,
        )
    else:
        open_q = getattr(queue_mod, "open_queue", None)
        enqueue_quantum = getattr(queue_mod, "enqueue_quantum", None)
        if not callable(open_q) or not callable(enqueue_quantum):
            raise RuntimeError("Queue backend not available (JobQueue/open_queue missing)")
        q = open_q(str(db_path))
        receipt = _call_with_supported_kwargs(
            enqueue_quantum,
            q=q,
            circuit=circuit,
            shots=shots,
            model=model,
            caller=caller,
            trap_ratio=trap_ratio,
            seed=seed,
        )

    if hasattr(receipt, "__dict__"):
        return dict(receipt.__dict__)  # type: ignore
    if isinstance(receipt, dict):
        return receipt
    return {"ok": True, "receipt": str(receipt)}


@app.command("enqueue-quantum")
def enqueue_quantum_cmd(
    circuit: Optional[str] = typer.Option(
        None, "--circuit", "-c", help="Circuit JSON string (mutually exclusive with --circuit-file)."
    ),
    circuit_file: Optional[Path] = typer.Option(
        None,
        "--circuit-file",
        "-f",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to circuit JSON file.",
    ),
    shots: int = typer.Option(256, "--shots", "-s", min=1, help="Number of shots/samples to run."),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="Optional device/model id (e.g., qpu-sim, provider:device)."
    ),
    caller: Optional[str] = typer.Option(
        None, "--caller", help="Optional caller address (anim1…). Used for attribution/policies if backend supports it."
    ),
    trap_ratio: Optional[float] = typer.Option(
        None,
        "--trap-ratio",
        help="Optional ratio (0..1] of trap circuits to include (backend-dependent).",
    ),
    seed: Optional[int] = typer.Option(
        None,
        "--seed",
        help="Optional deterministic seed for simulators (if supported).",
    ),
    backend: Optional[str] = typer.Option(
        None, "--backend", help="Force backend: host | aicf | queue (default: auto-detect)."
    ),
    queue_db: Path = typer.Option(
        Path(os.getenv("CAP_QUEUE_DB", "./capabilities_jobs.db")),
        "--queue-db",
        help="Queue DB path for 'queue' backend.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON only."),
) -> None:
    """
    Enqueue a Quantum job and print the receipt (task id, status, etc.).
    """
    circ = _load_circuit(circuit, circuit_file)

    tried = []
    error_last: Optional[Exception] = None

    def try_backend(name: str) -> Optional[Dict[str, Any]]:
        nonlocal error_last
        try:
            if name == "host":
                return _enqueue_via_host(circ, shots, model, caller, trap_ratio, seed)
            if name == "aicf":
                return _enqueue_via_aicf(circ, shots, model, caller, trap_ratio, seed)
            if name == "queue":
                return _enqueue_via_queue(circ, shots, model, caller, trap_ratio, seed, queue_db)
            raise ValueError(f"Unknown backend {name}")
        except Exception as e:
            error_last = e
            tried.append(f"{name}: {e}")
            return None

    # Forced backend path
    if backend:
        res = try_backend(backend)
        if res is None:
            msg = f"enqueue failed using backend '{backend}': {error_last}"
            if json_out:
                _pretty_print({"ok": False, "error": str(error_last), "tried": tried}, as_json=True)
            else:
                typer.echo(msg)
            raise typer.Exit(1)
        _pretty_print({"ok": True, "backend": backend, "receipt": res}, as_json=json_out)
        return

    # Auto-detect path
    for name in ("host", "aicf", "queue"):
        res = try_backend(name)
        if res is not None:
            _pretty_print({"ok": True, "backend": name, "receipt": res}, as_json=json_out)
            return

    # None succeeded
    if json_out:
        _pretty_print({"ok": False, "error": str(error_last or 'no backends available'), "tried": tried}, as_json=True)
    else:
        typer.echo("enqueue failed; tried backends:\n  - " + "\n  - ".join(tried))
    raise typer.Exit(2)


def register(root_app: "typer.Typer") -> None:
    """
    Hook for capabilities.cli to attach this command.
    """
    root_app.add_typer(app, name=COMMAND_NAME)
