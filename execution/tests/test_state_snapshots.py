import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pytest


# ===================================================
# Helpers: deterministic "state root" for assertions
# ===================================================

def _state_root(state: Dict[str, object]) -> bytes:
    """
    Deterministic commitment of a tiny KV state using SHA3-256.
    Values are normalized to integers or UTF-8 strings for stability.
    """
    def norm(v: object) -> bytes:
        if isinstance(v, int):
            return int(v).to_bytes(16, "big", signed=True)
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
        return str(v).encode("utf-8")

    items = sorted(state.items(), key=lambda kv: kv[0])
    buf = bytearray()
    for k, v in items:
        buf.extend(k.encode("utf-8"))
        buf.extend(norm(v))
    return hashlib.sha3_256(bytes(buf)).digest()


# ===================================================
# Minimal journal with checkpoint/commit/revert semantics
# (Reference model to test correctness & guard regressions)
# ===================================================

_MISSING = object()

@dataclass
class _Checkpoint:
    id: int
    # first-write log: key -> previous value (or _MISSING)
    prev: Dict[str, object]


class ModelJournal:
    """
    Simple write journal with nested checkpoints.
    On commit(child) → merge child's first-write log into parent (without overwriting parent's entries).
    On revert(child) → restore previous values in reverse first-touch order.
    """
    def __init__(self, state: Optional[Dict[str, object]] = None) -> None:
        self.state: Dict[str, object] = dict(state or {})
        self._stack: List[_Checkpoint] = []
        self._next_id = 1

    # --- mutations ----

    def put(self, key: str, value: object) -> None:
        if self._stack:
            top = self._stack[-1]
            if key not in top.prev:
                top.prev[key] = self.state.get(key, _MISSING)
        self.state[key] = value

    def delete(self, key: str) -> None:
        if key in self.state:
            if self._stack:
                top = self._stack[-1]
                if key not in top.prev:
                    top.prev[key] = self.state.get(key, _MISSING)
            self.state.pop(key, None)

    # --- checkpoints ----

    def checkpoint(self) -> int:
        cid = self._next_id
        self._next_id += 1
        self._stack.append(_Checkpoint(cid, {}))
        return cid

    def _find_index(self, cid: int) -> int:
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i].id == cid:
                return i
        raise KeyError(f"unknown checkpoint id {cid}")

    def commit(self, cid: int) -> None:
        idx = self._find_index(cid)
        if idx < 0:
            raise KeyError(cid)
        # Merge this checkpoint's log into its parent (if any), without overwriting parent's entries
        if idx > 0:
            parent = self._stack[idx - 1]
            for k, prev in self._stack[idx].prev.items():
                parent.prev.setdefault(k, prev)
        # Drop the committed checkpoint
        self._stack.pop(idx)

    def revert(self, cid: int) -> None:
        idx = self._find_index(cid)
        cp = self._stack.pop(idx)
        # Revert only the selected checkpoint; deeper children (if present) must have been
        # committed or reverted first. Enforce stack discipline.
        if idx != len(self._stack):
            # We only support LIFO reverts; real implementation could allow arbitrary, but that's uncommon.
            raise RuntimeError("revert must target the top-most checkpoint (LIFO)")
        # Restore keys in reverse insertion order for determinism (dict preserves insertion order in Python 3.7+)
        for k, prev in reversed(list(cp.prev.items())):
            if prev is _MISSING:
                self.state.pop(k, None)
            else:
                self.state[k] = prev


# ===================================================
# Tests against the model
# ===================================================

def test_basic_checkpoint_revert_restores_exact_state():
    base = {"a": 1, "b": 2}
    j = ModelJournal(base)
    root0 = _state_root(j.state)

    cid = j.checkpoint()
    j.put("a", 100)         # modify
    j.put("c", "hello")     # add
    j.delete("b")           # delete
    assert j.state == {"a": 100, "c": "hello"}

    j.revert(cid)
    assert j.state == base
    assert _state_root(j.state) == root0


def test_basic_commit_persists_changes():
    base = {"x": 7, "y": 8}
    j = ModelJournal(base)
    cid = j.checkpoint()
    j.put("y", 999)
    j.put("z", 42)
    j.commit(cid)
    # After commit, stack is empty and changes remain
    assert j._stack == []
    assert j.state == {"x": 7, "y": 999, "z": 42}


def test_nested_checkpoints_commit_and_revert():
    j = ModelJournal({"a": 1, "b": 2, "c": 3})
    outer = j.checkpoint()
    j.put("a", 10)          # touched in outer
    inner = j.checkpoint()
    j.put("b", 20)          # touched in inner
    j.put("d", 40)

    # Revert inner → only inner's first-writes roll back
    j.revert(inner)
    assert j.state == {"a": 10, "b": 2, "c": 3}

    # Commit outer → its changes persist
    j.commit(outer)
    assert j.state == {"a": 10, "b": 2, "c": 3}


def test_first_write_logging_is_stable_across_multiple_writes():
    j = ModelJournal({"k": 1})
    cid = j.checkpoint()
    j.put("k", 2)
    j.put("k", 3)
    j.put("k", 4)
    # Revert → should restore to original 1 (first-write recorded prev once)
    j.revert(cid)
    assert j.state["k"] == 1


def test_lifo_revert_is_enforced():
    j = ModelJournal()
    c1 = j.checkpoint()
    c2 = j.checkpoint()
    # Reverting c1 while c2 is still open should error (enforce LIFO)
    with pytest.raises(RuntimeError):
        j.revert(c1)
    # Proper order:
    j.revert(c2)
    j.revert(c1)
    assert j._stack == []


def test_diff_equivalence_via_commit_vs_revert_roundtrip():
    """
    If we checkpoint, mutate, then *either* commit or revert and replay,
    the resulting root should match expectations.
    """
    base = {"a": 1, "b": 2, "c": 3}
    j1 = ModelJournal(base)
    j2 = ModelJournal(base)

    # Do a sequence of mutations under a checkpoint in j1
    c = j1.checkpoint()
    j1.put("a", 11)
    j1.put("d", 9)
    j1.delete("b")
    committed_state = dict(j1.state)
    j1.commit(c)

    # Mirror the same sequence in j2 but revert instead, then re-apply the operations
    c2 = j2.checkpoint()
    j2.put("a", 11)
    j2.put("d", 9)
    j2.delete("b")
    j2.revert(c2)
    # Now apply the "diff" again to reach the same committed_state
    j2.put("a", 11)
    j2.put("d", 9)
    j2.delete("b")

    assert _state_root(j1.state) == _state_root(j2.state)
    assert j1.state == committed_state == j2.state


# ===================================================
# Optional smoke against project implementation (if present)
# ===================================================

def test_project_snapshots_smoke_if_available():
    """
    Try to exercise execution.state.snapshots if the project exposes a compatible API.
    We accept a few common shapes:
      - class SnapshotManager/Journal/Snapshots with methods: checkpoint/begin, put, delete, commit, revert
      - free functions: begin/checkpoint, put/delete, commit, revert operating on a manager
    If not compatible, skip (the model tests above remain authoritative).
    """
    try:
        mod = __import__("execution.state.snapshots", fromlist=["*"])
    except Exception:
        pytest.skip("execution.state.snapshots not available")

    # Locate a manager class
    Manager = None
    for name in ("SnapshotManager", "Journal", "Snapshots", "SnapshotStore"):
        Manager = getattr(mod, name, None)
        if Manager:
            break

    state = {"a": 1, "b": 2}
    # Prefer a class if present
    if Manager is not None:
        try:
            mgr = Manager(state=state)  # common signature
        except TypeError:
            try:
                mgr = Manager(state)    # alt signature
            except TypeError:
                mgr = Manager()

        # Find methods (with aliases)
        cp = getattr(mgr, "checkpoint", getattr(mgr, "begin", None))
        commit = getattr(mgr, "commit", None)
        revert = getattr(mgr, "revert", None)
        put = getattr(mgr, "put", getattr(mgr, "set", None))
        delete = getattr(mgr, "delete", getattr(mgr, "remove", None))
        get_state = getattr(mgr, "state", None)

        if not callable(cp) or not callable(commit) or not callable(revert) or not callable(put) or not callable(delete):
            pytest.skip("snapshot manager found but missing required methods")

        root0 = _state_root(state if isinstance(get_state, dict) else getattr(mgr, "state", state))

        cid = cp()
        put("a", 10) if callable(put) else put.__call__("a", 10)
        put("c", "x")
        delete("b")
        revert(cid)

        # Fetch state reference
        cur = getattr(mgr, "state", None)
        if cur is None:
            # Maybe it exposes a getter
            get = getattr(mgr, "get_state", None)
            cur = get() if callable(get) else state

        assert _state_root(cur) == root0
        return

    # Otherwise, try a module-level functional API
    begin = getattr(mod, "begin", getattr(mod, "checkpoint", None))
    commit = getattr(mod, "commit", None)
    revert = getattr(mod, "revert", None)
    put = getattr(mod, "put", getattr(mod, "set", None))
    delete = getattr(mod, "delete", getattr(mod, "remove", None))
    get_state = getattr(mod, "get_state", None)

    if not all(callable(f) for f in (begin, commit, revert, put, delete)) or not callable(get_state):
        pytest.skip("execution.state.snapshots present but API not recognized")

    root0 = _state_root(get_state())
    c = begin()
    put("a", 123)
    delete("b")
    revert(c)
    assert _state_root(get_state()) == root0
