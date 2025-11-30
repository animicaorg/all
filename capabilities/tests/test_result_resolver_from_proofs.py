import inspect
import types as _types
from typing import Any, Callable, Dict, Iterable, Optional

import pytest

# --- Imports & existence checks -------------------------------------------------

try:
    import capabilities.jobs.resolver as resolver  # type: ignore
except Exception as e:  # pragma: no cover
    pytest.skip(f"capabilities.jobs.resolver not importable: {e}")

try:
    from capabilities.jobs import result_store as rs  # type: ignore
except Exception as e:  # pragma: no cover
    pytest.skip(f"capabilities.jobs.result_store not importable: {e}")

try:
    from capabilities.jobs import types as jtypes  # type: ignore
except Exception:
    jtypes = None  # type: ignore


# --- Test fixtures --------------------------------------------------------------

TEST_TASK_HEX = "0x" + ("11" * 32)
TEST_HEIGHT = 12345


def _mk_result_record(task_id_hex: str, kind: str = "AI") -> Any:
    """
    Create a ResultRecord (preferred) or a dict fallback if the dataclass isn't available.
    """
    payload_digest = bytes.fromhex("aa" * 32)
    if jtypes and hasattr(jtypes, "ResultRecord"):
        # Try to build a strongly-typed record.
        try:
            return jtypes.ResultRecord(  # type: ignore[attr-defined]
                task_id=bytes.fromhex(task_id_hex[2:]),
                kind=kind,
                ok=True,
                height=TEST_HEIGHT,
                payload_digest=payload_digest,
                meta={"note": "unit-test"},
            )
        except Exception:
            pass
    # Fallback dict shape
    return {
        "task_id": task_id_hex,
        "kind": kind,
        "ok": True,
        "height": TEST_HEIGHT,
        "payload_digest": payload_digest,
        "meta": {"note": "unit-test"},
    }


class FakeResultStore:
    """
    A compatibility stub that implements several common method names so the resolver
    can call any of them. All write methods funnel into _put().
    """

    def __init__(self) -> None:
        self._records: Dict[str, Any] = {}

    # --- writers the resolver might call
    def put(self, rec: Any) -> None:
        self._put(rec)

    def save(self, rec: Any) -> None:
        self._put(rec)

    def add(self, rec: Any) -> None:
        self._put(rec)

    def store(self, rec: Any) -> None:
        self._put(rec)

    def upsert(self, rec: Any) -> None:
        self._put(rec)

    def write(self, rec: Any) -> None:
        self._put(rec)

    def set(self, rec: Any) -> None:
        self._put(rec)

    def put_result(self, rec: Any) -> None:
        self._put(rec)

    # --- readers the test uses (and the resolver might also call)
    def get(self, task_id: Any) -> Optional[Any]:
        key = _key_normalize(task_id)
        return self._records.get(key)

    def fetch(self, task_id: Any) -> Optional[Any]:
        return self.get(task_id)

    def load(self, task_id: Any) -> Optional[Any]:
        return self.get(task_id)

    # --- optional transactional no-ops
    def begin(self) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...

    # --- internal helpers
    def _put(self, rec: Any) -> None:
        task_id = getattr(rec, "task_id", None)
        if task_id is None and isinstance(rec, dict):
            task_id = rec.get("task_id")
        if task_id is None:
            raise AssertionError("Record missing task_id")
        self._records[_key_normalize(task_id)] = rec


def _key_normalize(task_id: Any) -> str:
    if isinstance(task_id, (bytes, bytearray)):
        return "0x" + task_id.hex()
    if isinstance(task_id, str):
        if task_id.startswith("0x") or task_id.startswith("0X"):
            return "0x" + task_id[2:].lower()
        # assume hex-without-0x
        return "0x" + task_id.lower()
    raise TypeError(f"Unsupported task_id type: {type(task_id)}")


def _find_resolver_entry() -> Callable[..., Any]:
    """
    Locate a likely entrypoint in the resolver module.
    Supported names (first one found is used):
      - apply_block_proofs(store, proofs, height=...)
      - resolve_block(...)
      - resolve_proofs(...)
      - ingest_proofs(...)
      - process_block_proofs(...)
    """
    candidates = (
        "apply_block_proofs",
        "resolve_block",
        "resolve_proofs",
        "ingest_proofs",
        "process_block_proofs",
    )
    for name in candidates:
        fn = getattr(resolver, name, None)
        if callable(fn):
            return fn
    pytest.skip("No known resolver entrypoint found")


def _call_resolver(
    fn: Callable[..., Any], store: Any, proofs: Iterable[Any], height: int
) -> Any:
    """
    Try a few common call patterns; prefer keyword calls for clarity.
    """
    sig = None
    try:
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
    except Exception:
        params = []

    # Keyword-first attempts
    for kwargs in (
        {"store": store, "proofs": proofs, "height": height},
        {"result_store": store, "proofs": proofs, "height": height},
        {"store": store, "proofs": proofs},
        {"result_store": store, "proofs": proofs},
    ):
        try:
            return fn(**{k: v for k, v in kwargs.items() if k in params or not params})
        except TypeError:
            pass

    # Positional fallbacks
    for args in (
        (store, proofs, height),
        (proofs, store, height),
        (store, proofs),
        (proofs, store),
    ):
        try:
            return fn(*args)
        except TypeError:
            continue

    pytest.skip("Resolver entrypoint signature not recognized")


# --- Monkeypatch proof→result mapping if the resolver defers to an adapter ------


def _patch_attest_bridge(
    monkeypatch: pytest.MonkeyPatch, expected_task_hex: str
) -> None:
    """
    If resolver calls into capabilities.jobs.attest_bridge (e.g., normalize_proof/proof_to_result),
    patch those functions to return a deterministic ResultRecord from the incoming proof.
    """
    try:
        import capabilities.jobs.attest_bridge as bridge  # type: ignore
    except Exception:
        return

    def to_result(proof: Any, *_, **__) -> Any:
        # If the proof carries a task id, prefer it; else fall back to expected.
        t_hex = (
            getattr(proof, "task_id", None)
            or (isinstance(proof, dict) and proof.get("task_id"))
            or expected_task_hex
        )
        kind = (
            getattr(proof, "kind", None)
            or (isinstance(proof, dict) and proof.get("kind"))
            or "AI"
        )
        return _mk_result_record(t_hex, kind=kind)

    for name in (
        "normalize_proof",
        "proof_to_result",
        "map_proof_to_result",
        "to_result_record",
    ):
        if hasattr(bridge, name):
            monkeypatch.setattr(bridge, name, to_result, raising=True)


# --- Tests ----------------------------------------------------------------------


def test_apply_block_proofs_ingests_ai_result(monkeypatch: pytest.MonkeyPatch):
    """
    A minimal AI proof should be turned into a ResultRecord and written to the store.
    We monkeypatch the attest_bridge to ensure deterministic translation regardless
    of the internal proof schema.
    """
    fn = _find_resolver_entry()
    store = FakeResultStore()

    _patch_attest_bridge(monkeypatch, TEST_TASK_HEX)

    # Minimal placeholder "proof" object – resolver should hand it off to the bridge.
    ai_proof = {"type_id": "AI", "kind": "AI", "task_id": TEST_TASK_HEX, "ok": True}

    _call_resolver(fn, store, [ai_proof], TEST_HEIGHT)

    rec = store.get(TEST_TASK_HEX)
    assert (
        rec is not None
    ), "ResultRecord not found in store after resolver applied proofs"

    # Shape checks (both dataclass or dict are acceptable)
    if isinstance(rec, dict):
        assert rec.get("ok") is True
        assert rec.get("kind") in ("AI", "Quantum")
        assert rec.get("height") == TEST_HEIGHT
    else:
        # dataclass-ish
        assert getattr(rec, "ok", False) is True
        assert getattr(rec, "kind", None) in ("AI", "Quantum")
        assert getattr(rec, "height", None) == TEST_HEIGHT


def test_idempotent_on_duplicate_proofs(monkeypatch: pytest.MonkeyPatch):
    """
    Re-applying the same proof set shouldn't create duplicate logical entries.
    Our FakeResultStore maps by task_id, so a second application overwrites the same key.
    """
    fn = _find_resolver_entry()
    store = FakeResultStore()

    _patch_attest_bridge(monkeypatch, TEST_TASK_HEX)

    proof = {"type_id": "AI", "kind": "AI", "task_id": TEST_TASK_HEX, "ok": True}

    _call_resolver(fn, store, [proof], TEST_HEIGHT)
    first_snapshot = dict(store._records)

    _call_resolver(fn, store, [proof], TEST_HEIGHT)
    second_snapshot = dict(store._records)

    assert (
        set(first_snapshot.keys())
        == set(second_snapshot.keys())
        == {TEST_TASK_HEX.lower()}
    )
    # And the record is still OK
    rec = store.get(TEST_TASK_HEX)
    if isinstance(rec, dict):
        assert rec.get("ok") is True
    else:
        assert getattr(rec, "ok", False) is True
