"""
omni_sdk.contracts.codegen
==========================

Generate ergonomic, typed Python client stubs from an Animica ABI.

The generated class wraps `omni_sdk.contracts.client.ContractClient` and
exposes per-function helpers:

- For *read-only* (view/pure) functions:
    def get(..., *, value: int = 0) -> <ReturnType>:
        return self._client.call("get", [...], value=value)

- For *state-changing* (non-view) functions:
    def send_inc(..., *, signer: PQSigner, nonce: int, max_fee: int,
                 gas_limit: int | None = None, value: int = 0,
                 await_receipt: bool = True, timeout_s: float = 60.0,
                 poll_interval_s: float = 0.5) -> dict:
        return self._client.send("inc", [...], ...)

Additionally, per-event helpers are emitted:

    def on_Transfer(self, receipt_or_logs) -> list[dict]:
        return events.filter_logs_by_event(self._abi, logs, "Transfer")

Quickstart
----------
    from omni_sdk.contracts.codegen import emit_python_client

    src = emit_python_client(abi, class_name="CounterClient")
    with open("counter_client.py", "w") as f:
        f.write(src)

    # Later:
    # from counter_client import CounterClient
    # c = CounterClient(rpc, "anim1...", chain_id=1)
"""

from __future__ import annotations

import keyword
import re
from dataclasses import dataclass
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Sequence,
                    Tuple)

# ABI normalization
try:
    from omni_sdk.types.abi import normalize_abi  # type: ignore
except Exception as _e:  # pragma: no cover
    raise RuntimeError(
        "omni_sdk.types.abi.normalize_abi is required for codegen"
    ) from _e


# ---------- Helpers to parse normalized ABI -----------------------------------


def _iter_functions(abi_norm: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    # Supports both {functions:{...}} and list-of-entries shapes
    if "functions" in abi_norm:
        fns = abi_norm["functions"]
        if isinstance(fns, Mapping):
            for name, f in fns.items():
                if isinstance(f, Mapping):
                    # Ensure name is present on the entry
                    f = dict(f)
                    f.setdefault("name", name)
                    yield f
        elif isinstance(fns, (list, tuple)):
            for f in fns:
                if isinstance(f, Mapping):
                    yield f
        return
    if isinstance(abi_norm, (list, tuple)):
        for e in abi_norm:
            if isinstance(e, Mapping) and str(e.get("type", "function")) == "function":
                yield e


def _iter_events(abi_norm: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    if "events" in abi_norm:
        evs = abi_norm["events"]
        if isinstance(evs, Mapping):
            for name, e in evs.items():
                if isinstance(e, Mapping):
                    ee = dict(e)
                    ee.setdefault("name", name)
                    yield ee
        elif isinstance(evs, (list, tuple)):
            for e in evs:
                if isinstance(e, Mapping):
                    yield e
        return
    if isinstance(abi_norm, (list, tuple)):
        for e in abi_norm:
            if isinstance(e, Mapping) and str(e.get("type", "event")) == "event":
                yield e


# ---------- Name & type mapping utilities ------------------------------------

_PY_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _py_ident(name: str) -> str:
    """Return a safe Python identifier (snake_case), avoiding keywords."""
    if not name:
        return "arg"
    # snake_case conversion for common cases: camelCase / PascalCase → snake_case
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    snake = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
    snake = re.sub(r"[^A-Za-z0-9_]", "_", snake)
    if not _PY_IDENT_RE.match(snake) or keyword.iskeyword(snake):
        snake = f"{snake}_"
    return snake


@dataclass(frozen=True)
class _TypeHint:
    py: str  # Python type hint as string
    is_bytes_like: bool = False


def _map_type(t: str) -> _TypeHint:
    """Map ABI scalar/container types to Python type hints."""
    t = str(t).strip()
    # Common scalars
    if t in ("bool",):
        return _TypeHint("bool")
    if t in ("string",):
        return _TypeHint("str")
    if t in ("address",):
        return _TypeHint("str")
    if t.startswith("bytes") and (t == "bytes" or t[5:].isdigit()):
        return _TypeHint("bytes", is_bytes_like=True)
    if t.startswith("uint") or t.startswith("int"):
        return _TypeHint("int")
    # Arrays: type[]
    if t.endswith("[]"):
        inner = _map_type(t[:-2]).py
        return _TypeHint(f"list[{inner}]")
    # Tuple-like: (t1,t2,...) → list[Any] (conservative)
    if t.startswith("(") and t.endswith(")"):
        return _TypeHint("list[Any]")
    # Fallback
    return _TypeHint("Any")


def _mutability(entry: Mapping[str, Any]) -> str:
    # Prefer EVM-like field names if present; otherwise look for booleans
    m = str(entry.get("stateMutability") or entry.get("mutability") or "").lower()
    if m in ("view", "pure", "readonly"):
        return "view"
    if bool(entry.get("constant", False)) is True:
        return "view"
    return "nonpayable"


def _unique_names(names: List[str]) -> List[str]:
    """De-duplicate overloaded function names by appending numeric suffixes."""
    seen: Dict[str, int] = {}
    out: List[str] = []
    for n in names:
        base = n
        if base not in seen:
            seen[base] = 1
            out.append(base)
            continue
        k = seen[base]
        seen[base] = k + 1
        out.append(f"{base}_{k}")
    return out


# ---------- Emission ----------------------------------------------------------

_HEADER = '''"""
Generated client stub for {title}

This file was generated by omni_sdk.contracts.codegen.emit_python_client.
Do not edit by hand. Consider committing this file to your repository.
"""
from __future__ import annotations

from typing import Any, Optional

from omni_sdk.contracts.client import ContractClient
from omni_sdk.contracts import events as _ev
from omni_sdk.wallet.signer import PQSigner
'''

_CLASS_TMPL = '''
class {class_name}:
    """Typed client for the "{contract_name}" contract.

    Parameters
    ----------
    rpc : RPC client with `.call(method, params)` (see omni_sdk.rpc.http.HttpClient)
    address : bech32m "anim1…" address of the deployed contract
    chain_id : int chain id used for signing domain separation
    """

    def __init__(self, rpc, address: str, chain_id: int):
        self._client = ContractClient(rpc=rpc, address=address, abi={abi_var}, chain_id=int(chain_id))
        self._abi = {abi_var}

    @property
    def address(self) -> str:
        return self._client.address

    @property
    def chain_id(self) -> int:
        return self._client.chain_id

    @classmethod
    def from_manifest(cls, rpc, address: str, chain_id: int, manifest: dict) -> "{class_name}":
        """Construct from a manifest that contains an 'abi' field."""
        abi = manifest.get("abi")
        if abi is None:
            raise ValueError("manifest missing 'abi'")
        return cls(rpc=rpc, address=address, chain_id=chain_id)  # ABI is embedded at codegen time
'''

_FN_VIEW_TMPL = '''
    def {py_name}(self{sig_args}, *, value: int = 0) -> {ret_py}:
        """Read-only call: {fn_name}({human_sig}) -> {ret_py}"""
        data = self._client.call("{fn_name}", [{arg_names}], value=int(value))
        return data  # already ABI-decoded
'''

_FN_SEND_TMPL = '''
    def send_{py_name}(self{sig_args}, *, signer: PQSigner, nonce: int, max_fee: int,
                       gas_limit: int | None = None, value: int = 0,
                       await_receipt: bool = True, timeout_s: float = 60.0,
                       poll_interval_s: float = 0.5) -> dict:
        """State-changing tx: {fn_name}({human_sig})"""
        return self._client.send(
            "{fn_name}",
            [{arg_names}],
            signer=signer,
            nonce=int(nonce),
            max_fee=int(max_fee),
            gas_limit=int(gas_limit) if gas_limit is not None else None,
            value=int(value),
            await_receipt=await_receipt,
            timeout_s=float(timeout_s),
            poll_interval_s=float(poll_interval_s),
        )
'''

_EVENT_TMPL = '''
    # Events — "{event_name}"
    def on_{event_py}(self, receipt_or_logs: dict | list[dict]) -> list[dict]:
        """Return all decoded "{event_name}" events from a receipt or logs list."""
        # We intentionally pass the ABI we embedded at codegen time.
        if isinstance(receipt_or_logs, dict) and "logs" in receipt_or_logs:
            logs = receipt_or_logs["logs"]
        else:
            logs = receipt_or_logs
        return _ev.filter_logs_by_event(self._abi, logs, "{event_name}")
'''


def _emit_fn(entry: Mapping[str, Any], used_names: Dict[str, int]) -> Tuple[str, str]:
    """Return (view_src, send_src) for a single function entry (some may be empty)."""
    name = str(entry.get("name", "fn"))
    inputs = entry.get("inputs", []) or []
    outputs = entry.get("outputs", []) or []
    mut = _mutability(entry)

    # Prepare Python-safe parameter names & hints
    arg_defs: List[str] = []
    arg_names: List[str] = []
    human_parts: List[str] = []
    for arg in inputs:
        if not isinstance(arg, Mapping):
            continue
        aname = _py_ident(str(arg.get("name") or "arg"))
        atype = _map_type(str(arg.get("type", "bytes")))
        arg_defs.append(f"{aname}: {atype.py}")
        arg_names.append(aname)
        human_parts.append(f"{aname}: {atype.py}")

    # Return type hint for view functions (first output or tuple/list)
    if not outputs:
        ret_py = "None"
    elif len(outputs) == 1:
        ret_py = _map_type(str(outputs[0].get("type", "bytes"))).py
    else:
        # Multi-returns → tuple[Any, ...]
        ret_py = "tuple[" + ",".join("Any" for _ in outputs) + "]"

    base_py_name = _py_ident(name)
    # Deduplicate method names across overloads
    idx = used_names.get(base_py_name, 0)
    used_names[base_py_name] = idx + 1
    py_name = base_py_name if idx == 0 else f"{base_py_name}_{idx}"

    sig_args = ""
    if arg_defs:
        sig_args = ", " + ", ".join(arg_defs)

    human_sig = ", ".join(human_parts) if human_parts else ""

    view_src = ""
    send_src = ""

    if mut == "view":
        view_src = _FN_VIEW_TMPL.format(
            py_name=py_name,
            sig_args=sig_args,
            ret_py=ret_py,
            fn_name=name,
            human_sig=human_sig,
            arg_names=", ".join(arg_names),
        )
    else:
        # Even for nonpayable, some nodes may allow static simulate; we still emit only send_*
        send_src = _FN_SEND_TMPL.format(
            py_name=py_name,
            sig_args=sig_args,
            fn_name=name,
            human_sig=human_sig,
            arg_names=", ".join(arg_names),
        )

    return view_src, send_src


def _emit_event(ev: Mapping[str, Any]) -> str:
    name = str(ev.get("name", "Event"))
    event_py = _py_ident(name)
    return _EVENT_TMPL.format(event_name=name, event_py=event_py)


def emit_python_client(
    abi: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    class_name: str = "ContractClientStub",
    contract_name: Optional[str] = None,
    title: Optional[str] = None,
    include_events: bool = True,
) -> str:
    """
    Generate Python source code for a client class from the given ABI.

    Parameters
    ----------
    abi : ABI object (list or dict). Will be normalized for consistency.
    class_name : Name of the generated class.
    contract_name : Human-friendly contract name shown in docstrings (defaults to class_name).
    title : Optional header title string.
    include_events : If True, emit event decoding helpers.

    Returns
    -------
    str : Python source code.
    """
    abi_norm = normalize_abi(abi)  # type: ignore[arg-type]
    contract_name = contract_name or class_name
    title = title or f"{class_name} ({contract_name})"

    # We embed the normalized ABI literal in the generated file as `_abi` binding.
    import json

    abi_literal = json.dumps(abi_norm, separators=(",", ":"), ensure_ascii=False)

    header = _HEADER.format(title=title)
    class_src = _CLASS_TMPL.format(
        class_name=class_name,
        contract_name=contract_name,
        abi_var="_abi",
    )

    # Emit function methods
    used_names: Dict[str, int] = {}
    view_parts: List[str] = []
    send_parts: List[str] = []
    for fn in _iter_functions(abi_norm):
        vsrc, ssrc = _emit_fn(fn, used_names)
        if vsrc:
            view_parts.append(vsrc)
        if ssrc:
            send_parts.append(ssrc)

    # Emit event helpers
    event_parts: List[str] = []
    if include_events:
        for ev in _iter_events(abi_norm):
            if bool(ev.get("anonymous", False)):
                continue
            event_parts.append(_emit_event(ev))

    body = "".join(view_parts) + "".join(send_parts) + "".join(event_parts)

    # Assemble file
    src = header
    src += f"\n_abi = {abi_literal}\n"
    src += class_src
    src += body
    src += f'\n__all__ = ["{class_name}"]\n'
    return src


def write_python_client(
    output_path: str,
    abi: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    class_name: str = "ContractClientStub",
    contract_name: Optional[str] = None,
    title: Optional[str] = None,
    include_events: bool = True,
) -> None:
    """
    Generate and write the client stub to `output_path`.
    """
    src = emit_python_client(
        abi,
        class_name=class_name,
        contract_name=contract_name,
        title=title,
        include_events=include_events,
    )
    import os

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(src)


__all__ = ["emit_python_client", "write_python_client"]
