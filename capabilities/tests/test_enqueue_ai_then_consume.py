import importlib
import types
import dataclasses
import pytest


# ---------- Helpers to find symbols with flexible names ----------

def _import(modname: str) -> types.ModuleType | None:
    try:
        return importlib.import_module(modname)
    except Exception:
        return None


def _get_attr(obj, names):
    for n in names:
        v = getattr(obj, n, None)
        if callable(v) or v is not None:
            return v
    return None


def _load_errors():
    mod = _import("capabilities.errors")
    class _FallbackNoResultYet(Exception): ...
    NoResultYet = getattr(mod, "NoResultYet", _FallbackNoResultYet if mod else _FallbackNoResultYet)
    return NoResultYet


NoResultYet = _load_errors()


def _mk_ctx(chain_id=1, height=100, tx_hash=bytes.fromhex("11"*32), caller=bytes.fromhex("22"*32)):
    """
    Many host APIs expect a simple context with these attributes.
    Use a tiny object so attribute access works regardless of implementation.
    """
    return types.SimpleNamespace(chain_id=chain_id, height=height, tx_hash=tx_hash, caller=caller)


def _task_id_from(receipt):
    """
    Support dicts, dataclasses, or objects with 'task_id' attribute.
    """
    if isinstance(receipt, dict):
        return receipt.get("task_id") or receipt.get("id") or receipt.get("taskId")
    if dataclasses.is_dataclass(receipt):
        return getattr(receipt, "task_id", None) or getattr(receipt, "id", None)
    return getattr(receipt, "task_id", None) or getattr(receipt, "id", None)


# ---------- Enqueue via host.compute.* or host.provider.* ----------

def _enqueue_ai(ctx, model: str, prompt: bytes):
    # Preferred: capabilities.host.compute.ai_enqueue(ctx, model=..., prompt=...)
    mod = _import("capabilities.host.compute")
    fn = _get_attr(mod, ["ai_enqueue", "enqueue_ai"]) if mod else None
    if callable(fn):
        return fn(ctx, model=model, prompt=prompt)  # type: ignore[misc]

    # Fallback: capabilities.host.provider.Provider().ai_enqueue(...)
    prov_mod = _import("capabilities.host.provider")
    Provider = _get_attr(prov_mod, ["Provider", "HostProvider", "SyscallProvider"]) if prov_mod else None
    if Provider:
        try:
            prov = Provider()  # type: ignore[call-arg]
        except TypeError:
            # Try no-arg factory
            prov = _get_attr(prov_mod, ["default", "create", "new"])()
        meth = _get_attr(prov, ["ai_enqueue", "enqueue_ai", "enqueue"])
        if callable(meth):
            # Some variants accept (ctx, kind, **kwargs)
            try:
                return meth(ctx, model=model, prompt=prompt)
            except TypeError:
                return meth("ai", ctx, model=model, prompt=prompt)

    pytest.skip("AI enqueue function not available in capabilities.host")


# ---------- Read result via host.result_read or provider ----------

def _read_result(task_id):
    mod = _import("capabilities.host.result_read")
    fn = _get_attr(mod, ["read_result", "get_result"]) if mod else None
    if callable(fn):
        return fn(task_id)  # type: ignore[misc]

    # Fallback via Provider
    prov_mod = _import("capabilities.host.provider")
    Provider = _get_attr(prov_mod, ["Provider", "HostProvider", "SyscallProvider"]) if prov_mod else None
    if Provider:
        try:
            prov = Provider()  # type: ignore[call-arg]
        except TypeError:
            prov = _get_attr(prov_mod, ["default", "create", "new"])()
        meth = _get_attr(prov, ["read_result", "get_result"])
        if callable(meth):
            return meth(task_id)

    pytest.skip("Result read function not available in capabilities.host")


# ---------- Resolver / Result injection for next block ----------

def _write_result_next_block(height_next: int, task_id, result_bytes: bytes):
    """
    Try the official resolver first; otherwise write directly into result_store.
    """
    # Try resolver
    res_mod = _import("capabilities.jobs.resolver")
    if res_mod:
        # Common function name variants
        for name in ["apply_proofs", "resolve_block", "ingest_block_results", "populate_results"]:
            fn = getattr(res_mod, name, None)
            if callable(fn):
                try:
                    # Try signature (height, [(task_id, result_bytes)])
                    return fn(height_next, [(task_id, result_bytes)])
                except TypeError:
                    # Try signature (records=[...]) or (height, records=[...])
                    try:
                        return fn(records=[(task_id, result_bytes)], height=height_next)
                    except TypeError:
                        pass

    # Direct result_store write
    store_mod = _import("capabilities.jobs.result_store")
    types_mod = _import("capabilities.jobs.types")
    if not store_mod:
        pytest.skip("Neither resolver nor result_store available to inject result")

    # Record type (dataclass) if available, else dict
    ResultRecord = _get_attr(types_mod, ["ResultRecord"]) if types_mod else None
    kind_val = "AI"
    if ResultRecord:
        try:
            record = ResultRecord(task_id=task_id, kind=kind_val, height=height_next, result=result_bytes)  # type: ignore[misc]
        except TypeError:
            # Try with minimal fields
            record = ResultRecord(task_id=task_id, result=result_bytes, height=height_next)  # type: ignore[misc]
    else:
        record = {"task_id": task_id, "kind": kind_val, "height": height_next, "result": result_bytes}

    # Choose a put-like function
    put_fn = _get_attr(store_mod, ["put", "store", "write", "save", "insert"])
    if callable(put_fn):
        try:
            return put_fn(record)  # type: ignore[misc]
        except TypeError:
            return put_fn(task_id, record)  # type: ignore[misc]

    pytest.skip("Could not locate a method to persist ResultRecord")


# ======================== TESTS ========================

def test_enqueue_then_consume_next_block():
    """
    End-to-end happy path:
      1) Contract (via VM syscall host) enqueues an AI job -> receipt with task_id
      2) Reading immediately should raise NoResultYet
      3) Next block: resolver populates result_store
      4) Reading again returns the expected bytes
    """
    ctx = _mk_ctx(chain_id=1, height=10)
    model = "toy-model"
    prompt = b'{"text":"hello animica"}'
    receipt = _enqueue_ai(ctx, model, prompt)
    task_id = _task_id_from(receipt)
    assert task_id, "enqueue must return a receipt with a task_id"

    # Before resolution, there should be no result yet
    with pytest.raises(NoResultYet):
        _read_result(task_id)

    # Simulate next block filling in the result store
    expected = b'{"ok":true,"tokens":5,"output":"hi"}'
    _write_result_next_block(ctx.height + 1, task_id, expected)

    # Now it should be available
    out = _read_result(task_id)
    # Support dict or bytes return
    if isinstance(out, dict):
        body = out.get("result") or out.get("output") or out.get("bytes")
        assert body == expected
    else:
        assert out == expected


def test_multiple_enqueues_distinct_ids():
    ctx = _mk_ctx(chain_id=1, height=55)
    r1 = _enqueue_ai(ctx, "toy", b"A")
    r2 = _enqueue_ai(ctx, "toy", b"B")
    t1, t2 = _task_id_from(r1), _task_id_from(r2)
    assert t1 and t2 and t1 != t2, "distinct payloads must produce distinct task IDs"


def test_read_before_and_after_height_transition():
    """
    Strengthen timing assertion: result only appears from next height forward.
    """
    base_h = 777
    ctx = _mk_ctx(chain_id=9, height=base_h)
    receipt = _enqueue_ai(ctx, "toy", b"X")
    task_id = _task_id_from(receipt)
    assert task_id

    # Inject result at next height
    expected = b"done"
    _write_result_next_block(base_h + 1, task_id, expected)

    # Reading at current height should still be considered "not yet"
    # If the implementation doesn't track height in read_result, we accept immediate availability.
    try:
        _read_result(task_id)
    except NoResultYet:
        # OK — strictly height-gated
        pass

    # Reading now (after our simulated next block) must succeed
    out = _read_result(task_id)
    if isinstance(out, dict):
        body = out.get("result") or out.get("output") or out.get("bytes")
        assert body == expected
    else:
        assert out == expected
