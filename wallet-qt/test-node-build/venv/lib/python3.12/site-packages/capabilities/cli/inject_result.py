"""
capabilities.cli.inject_result
==============================

Manually inject a completed *ResultRecord* into the capabilities
result-store. This is a **devnet/testing** helper so contracts can
`read_result(task_id)` without needing a real off-chain worker.

It writes directly to the results KV (and any attached indexes the
store maintains). By default it *won't* overwrite an existing record
unless you pass --overwrite.

Examples:
  # Minimal: mark task as success with a tiny JSON result payload
  python -m capabilities.cli inject-result \
    --task-id 0xabc123 \
    --status success \
    --result-json '{"answer":42}'

  # Attach a proof blob from a file and set the height it became available
  python -m capabilities.cli inject-result \
    --task-id 0xfeed \
    --kind ai \
    --producer dev-worker-1 \
    --height 12345 \
    --proof-file ./attestation.json \
    --result-json-file ./out.json

  # Inject raw bytes (e.g., model output) and allow overwriting if it exists
  python -m capabilities.cli inject-result \
    --task-id T123 \
    --status success \
    --result-bytes-file ./output.bin \
    --bytes-encoding base64 \
    --overwrite

This module exposes `register(app)` so capabilities/cli/__init__.py can
discover and add the command dynamically.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import asdict, is_dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import typer  # type: ignore
except Exception as e:  # pragma: no cover
    raise ImportError("Typer is required. Install with: pip install typer[all]") from e

app = typer.Typer(
    name="inject-result",
    help="Manually inject a ResultRecord into the result-store (dev/test only).",
    add_completion=False,
)

COMMAND_NAME = "inject-result"


# --------------------------- helpers ---------------------------


def _read_json_file(path: Path) -> Any:
    with path.open("rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _maybe_json(s: Optional[str]) -> Optional[Any]:
    if s is None:
        return None
    try:
        return json.loads(s)
    except Exception:
        typer.echo(
            "ERROR: --result-json/--meta-json/--proof-json must be valid JSON.",
            err=True,
        )
        raise typer.Exit(2)


def _read_bytes(path: Path) -> bytes:
    with path.open("rb") as fh:
        return fh.read()


def _b64(x: bytes) -> str:
    return base64.b64encode(x).decode("ascii")


def _to_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj):
        try:
            return asdict(obj)  # type: ignore
        except Exception:
            return obj.__dict__  # type: ignore
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)  # type: ignore
    return {"value": str(obj)}


def _open_result_store(path: Optional[Path]):
    """
    Try several shapes:
      - capabilities.jobs.result_store.ResultStore(path)
      - capabilities.jobs.result_store.open_store(path=...)
      - module-level get_store()
    """
    mod = import_module("capabilities.jobs.result_store")
    store = None

    RS = getattr(mod, "ResultStore", None)
    if RS is not None:
        try:
            store = RS(str(path) if path else None)  # type: ignore
        except TypeError:
            # Some impls may not take a path; try no-arg
            store = RS()  # type: ignore

    if store is None:
        open_store = getattr(mod, "open_store", None)
        if callable(open_store):
            # accept either positional or kw
            try:
                store = open_store(str(path) if path else None)  # type: ignore
            except TypeError:
                store = open_store(path=str(path) if path else None)  # type: ignore

    if store is None:
        get_store = getattr(mod, "get_store", None)
        if callable(get_store):
            store = get_store()  # type: ignore

    if store is None:
        typer.echo("ERROR: could not initialize result-store backend.", err=True)
        raise typer.Exit(1)

    return mod, store


def _store_put(mod, store, record: Dict[str, Any], overwrite: bool) -> None:
    """
    Try common method names:
      - store.set_result(task_id, record, overwrite=...)
      - store.put(record, overwrite=...)
      - store.upsert(record)
      - module.set_result(store, record, overwrite=...)
    """
    task_id = record.get("task_id") or record.get("id")
    # Prefer instance with task_id + record
    for name in ("set_result", "write_result", "put_result"):
        fn = getattr(store, name, None)
        if callable(fn):
            try:
                fn(task_id, record, overwrite=overwrite)  # type: ignore
                return
            except TypeError:
                # maybe signature without overwrite
                fn(task_id, record)  # type: ignore
                return

    # Try generic put/upsert(record)
    for name in ("put", "upsert", "insert", "write", "save"):
        fn = getattr(store, name, None)
        if callable(fn):
            try:
                fn(record, overwrite=overwrite)  # type: ignore
            except TypeError:
                fn(record)  # type: ignore
            return

    # Try module-level functions
    for name in ("set_result", "put_result"):
        fn = getattr(mod, name, None)
        if callable(fn):
            try:
                fn(store, task_id, record, overwrite=overwrite)  # type: ignore
            except TypeError:
                fn(store, task_id, record)  # type: ignore
            return

    typer.echo(
        "ERROR: result-store backend does not expose a known put/upsert API.", err=True
    )
    raise typer.Exit(1)


# --------------------------- CLI ---------------------------


@app.command("inject-result")
def inject_result_cmd(
    task_id: str = typer.Option(
        ...,
        "--task-id",
        help="Task ID to write (H(chainId|height|tx|caller|payload) or app-defined).",
    ),
    kind: Optional[str] = typer.Option(
        None, "--kind", help="Job kind: ai|quantum (optional)."
    ),
    status: str = typer.Option(
        "success", "--status", help="Status string, e.g. success|error|failed."
    ),
    result_json: Optional[str] = typer.Option(
        None, "--result-json", help="Inline JSON string for result payload."
    ),
    result_json_file: Optional[Path] = typer.Option(
        None, "--result-json-file", help="Read JSON payload from file."
    ),
    result_bytes_file: Optional[Path] = typer.Option(
        None, "--result-bytes-file", help="Read raw bytes payload from file."
    ),
    bytes_encoding: str = typer.Option(
        "base64",
        "--bytes-encoding",
        help="If using --result-bytes-file, annotate encoding: base64|hex|raw (stored as-is).",
    ),
    proof_json: Optional[str] = typer.Option(
        None, "--proof-json", help="Inline JSON attestation/proof object."
    ),
    proof_file: Optional[Path] = typer.Option(
        None, "--proof-file", help="Load attestation/proof JSON from file."
    ),
    meta_json: Optional[str] = typer.Option(
        None, "--meta-json", help="Inline JSON for misc metadata."
    ),
    producer: Optional[str] = typer.Option(
        None, "--producer", help="Producer ID (e.g., worker name / device id)."
    ),
    caller: Optional[str] = typer.Option(
        None, "--caller", help="Caller account (for indexing; optional)."
    ),
    height: Optional[int] = typer.Option(
        None, "--height", help="Block height the result became available (optional)."
    ),
    timestamp: Optional[str] = typer.Option(
        None,
        "--timestamp",
        help="ISO 8601 timestamp override (defaults to now on write if store supports it).",
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Allow overwriting an existing record."
    ),
    store_db: Optional[Path] = typer.Option(
        Path(os.getenv("CAP_RESULT_DB", "./capabilities_results.db")),
        "--store-db",
        help="Path to the result-store DB, if your backend uses one.",
    ),
) -> None:
    """
    Inject a completed ResultRecord for `task_id` into the local result-store.
    """
    if (
        sum(x is not None for x in (result_json, result_json_file, result_bytes_file))
        > 1
    ):
        typer.echo(
            "ERROR: choose only one of --result-json, --result-json-file, or --result-bytes-file.",
            err=True,
        )
        raise typer.Exit(2)

    # Compose result payload
    result_payload: Any = None
    result_encoding: Optional[str] = None

    if result_json is not None:
        result_payload = _maybe_json(result_json)
    elif result_json_file is not None:
        result_payload = _read_json_file(result_json_file)
    elif result_bytes_file is not None:
        raw = _read_bytes(result_bytes_file)
        enc = bytes_encoding.lower().strip()
        if enc not in ("base64", "hex", "raw"):
            typer.echo("ERROR: --bytes-encoding must be base64|hex|raw.", err=True)
            raise typer.Exit(2)
        if enc == "base64":
            result_payload = {"bytes_b64": _b64(raw)}
        elif enc == "hex":
            result_payload = {"bytes_hex": raw.hex()}
        else:
            # raw bytes (not JSON-safe); store directly and annotate encoding
            result_payload = raw
        result_encoding = enc

    # Compose proof/meta
    proof_obj: Any = None
    if proof_json is not None and proof_file is not None:
        typer.echo("ERROR: choose only one of --proof-json or --proof-file.", err=True)
        raise typer.Exit(2)
    if proof_json is not None:
        proof_obj = _maybe_json(proof_json)
    elif proof_file is not None:
        proof_obj = _read_json_file(proof_file)

    meta_obj: Dict[str, Any] = {}
    if meta_json is not None:
        m = _maybe_json(meta_json)
        if not isinstance(m, dict):
            typer.echo("ERROR: --meta-json must decode to a JSON object.", err=True)
            raise typer.Exit(2)
        meta_obj = m

    # Open store
    mod, store = _open_result_store(store_db)

    # Build a flexible record; we include multiple synonymous fields so it fits
    # various backend schemas without needing a strict version pin.
    record: Dict[str, Any] = {
        "task_id": task_id,
        "id": task_id,  # alias
        "status": status,
        "ok": status.lower() in ("ok", "success", "succeeded", "true"),
        "result": result_payload,  # common
        "output": result_payload,  # alias
        "payload": result_payload,  # alias
        "result_encoding": result_encoding,
        "proof": proof_obj,
        "meta": meta_obj,
        "producer_id": producer,
        "producer": producer,
        "caller": caller,
        "produced_height": height,
        "height": height,
        "timestamp": timestamp,  # backends may override with 'now'
        "kind": kind,
        # Optional error message location (even for success it's harmless)
        "error": (
            None
            if status.lower() in ("ok", "success", "succeeded")
            else (meta_obj.get("error") if meta_obj else None)
        ),
    }

    # Strip Nones to keep the record tidy
    record = {k: v for k, v in record.items() if v is not None}

    # Write
    _store_put(mod, store, record, overwrite=overwrite)

    # Pretty echo a compact summary
    shown = {
        "task_id": record.get("task_id") or record.get("id"),
        "status": record.get("status"),
        "ok": record.get("ok"),
        "kind": record.get("kind"),
        "height": record.get("produced_height") or record.get("height"),
        "producer": record.get("producer_id") or record.get("producer"),
        "caller": record.get("caller"),
        "result_keys": (
            sorted(record.get("result", {}).keys())
            if isinstance(record.get("result"), dict)
            else type(record.get("result")).__name__
        ),
    }
    typer.echo(json.dumps(shown, indent=2, sort_keys=True))


def register(root_app: "typer.Typer") -> None:
    """
    Hook for capabilities.cli to attach this command.
    """
    root_app.add_typer(app, name=COMMAND_NAME)
