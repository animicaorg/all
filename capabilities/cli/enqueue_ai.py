"""
capabilities.cli.enqueue_ai
===========================

Dev helper to enqueue an **AI** job into the Animica capabilities stack.

It tries several backends (in this order), using whichever is available
in your current checkout/install:

1) capabilities.host.compute.ai_enqueue(...)
2) capabilities.adapters.aicf.enqueue_ai(...)
3) capabilities.jobs.queue.JobQueue.enqueue_ai(...)

Usage:
    python -m capabilities.cli enqueue-ai --model tiny-llama --prompt "hello"
    python -m capabilities.cli enqueue-ai --model instruct \
        --prompt-file prompt.txt --caller anim1xyz...

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

# Optional types (best-effort imports; CLI will degrade gracefully)
try:
    from capabilities.jobs.types import JobKind  # type: ignore
except Exception:  # pragma: no cover
    JobKind = None  # type: ignore


app = typer.Typer(
    name="enqueue-ai",
    help="Enqueue an AI job (dev helper). Tries host/aicf/queue backends.",
    add_completion=False,
)

COMMAND_NAME = "enqueue-ai"  # allows mount by name if register() not used


def _read_prompt(prompt: Optional[str], prompt_file: Optional[Path]) -> bytes:
    if prompt_file is not None:
        data = prompt_file.read_bytes()
        return data
    if prompt is None:
        raise typer.BadParameter("Provide either --prompt or --prompt-file")
    return prompt.encode("utf-8")


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


def _enqueue_via_host(model: str, payload: bytes, caller: Optional[str]) -> Dict[str, Any]:
    """
    capabilities.host.compute.ai_enqueue(...)
    """
    from importlib import import_module

    compute = import_module("capabilities.host.compute")
    fn = getattr(compute, "ai_enqueue", None)
    if not callable(fn):
        raise RuntimeError("capabilities.host.compute.ai_enqueue not available")
    receipt = _call_with_supported_kwargs(fn, model=model, prompt=payload, caller=caller)
    # Normalize to dict
    if hasattr(receipt, "__dict__"):
        return dict(receipt.__dict__)  # type: ignore
    if isinstance(receipt, dict):
        return receipt
    # Last-resort representation
    return {"ok": True, "receipt": str(receipt)}


def _enqueue_via_aicf(model: str, payload: bytes, caller: Optional[str]) -> Dict[str, Any]:
    """
    capabilities.adapters.aicf.enqueue_ai(...)
    """
    from importlib import import_module

    aicf = import_module("capabilities.adapters.aicf")
    fn = getattr(aicf, "enqueue_ai", None)
    if not callable(fn):
        raise RuntimeError("capabilities.adapters.aicf.enqueue_ai not available")
    receipt = _call_with_supported_kwargs(fn, model=model, prompt=payload, caller=caller)
    if hasattr(receipt, "__dict__"):
        return dict(receipt.__dict__)  # type: ignore
    if isinstance(receipt, dict):
        return receipt
    return {"ok": True, "receipt": str(receipt)}


def _enqueue_via_queue(
    model: str,
    payload: bytes,
    caller: Optional[str],
    db_path: Path,
) -> Dict[str, Any]:
    """
    capabilities.jobs.queue.JobQueue.enqueue_ai(...)
    """
    from importlib import import_module

    queue_mod = import_module("capabilities.jobs.queue")
    # Support either a class `JobQueue` or functions `open_queue()/enqueue_ai()`
    JobQueue = getattr(queue_mod, "JobQueue", None)
    if JobQueue is not None:
        q = JobQueue(str(db_path))
        receipt = _call_with_supported_kwargs(q.enqueue_ai, model=model, prompt=payload, caller=caller)  # type: ignore[attr-defined]
    else:
        open_q = getattr(queue_mod, "open_queue", None)
        enqueue_ai = getattr(queue_mod, "enqueue_ai", None)
        if not callable(open_q) or not callable(enqueue_ai):
            raise RuntimeError("Queue backend not available (JobQueue/open_queue missing)")
        q = open_q(str(db_path))
        receipt = _call_with_supported_kwargs(enqueue_ai, q=q, model=model, prompt=payload, caller=caller)

    if hasattr(receipt, "__dict__"):
        return dict(receipt.__dict__)  # type: ignore
    if isinstance(receipt, dict):
        return receipt
    return {"ok": True, "receipt": str(receipt)}


@app.command("enqueue-ai")
def enqueue_ai_cmd(
    model: str = typer.Option(..., "--model", "-m", help="Model id/name (e.g., tiny-llama, instruct)."),
    prompt: Optional[str] = typer.Option(
        None, "--prompt", "-p", help="Prompt string (mutually exclusive with --prompt-file)."
    ),
    prompt_file: Optional[Path] = typer.Option(
        None, "--prompt-file", "-f", exists=True, file_okay=True, dir_okay=False, readable=True, help="Prompt file."
    ),
    caller: Optional[str] = typer.Option(
        None, "--caller", help="Optional caller address (anim1…). Used for attribution/policies if backend supports it."
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
    Enqueue an AI job and print the receipt (task id, status, etc.).
    """
    payload = _read_prompt(prompt, prompt_file)

    tried = []
    error_last: Optional[Exception] = None

    def try_backend(name: str) -> Optional[Dict[str, Any]]:
        nonlocal error_last
        try:
            if name == "host":
                return _enqueue_via_host(model, payload, caller)
            if name == "aicf":
                return _enqueue_via_aicf(model, payload, caller)
            if name == "queue":
                return _enqueue_via_queue(model, payload, caller, queue_db)
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
