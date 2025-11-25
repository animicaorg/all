import inspect
import types
from typing import Any, Callable, Dict, List, Optional

import pytest


def _import_orchestrator():
    try:
        import importlib

        return importlib.import_module("mining.orchestrator")
    except Exception:
        return None


def _find_entrypoint(mod) -> Dict[str, Any]:
    """
    Try to discover a callable entrypoint to run the orchestrator exactly once.
    Supports either:
      - function: run_once(...) or mine_once(...)
      - class: MinerOrchestrator / Orchestrator with .run_once() / .start()/.stop()
    """
    entry: Dict[str, Any] = {}

    # Function-style
    for fn in ("run_once", "mine_once"):
        f = getattr(mod, fn, None)
        if callable(f):
            entry["kind"] = "fn"
            entry["callable"] = f
            return entry

    # Class-style
    for cls_name in ("MinerOrchestrator", "MiningOrchestrator", "Orchestrator"):
        cls = getattr(mod, cls_name, None)
        if cls and isinstance(cls, type):
            # method resolve
            for m in ("run_once", "runOne", "run", "start"):
                if callable(getattr(cls, m, None)):
                    entry["kind"] = "class"
                    entry["class"] = cls
                    entry["method"] = m
                    return entry

    return entry


def _patch_many(monkeypatch, mod, targets: Dict[str, Callable]):
    """
    Patch a set of symbol paths on the orchestrator module with fallbacks.
    We patch both module attributes and possibly directly-imported functions.
    """
    for name, fn in targets.items():
        # 1) Top-level symbol (e.g., mining.orchestrator.pack_candidate)
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, fn, raising=False)

        # 2) Namespaced object (e.g., mining.orchestrator.header_packer.pack_candidate)
        parts = name.split(".")
        if len(parts) > 1:
            head = getattr(mod, parts[0], None)
            if head is None:
                # create a namespace on-demand
                head = types.SimpleNamespace()
                monkeypatch.setattr(mod, parts[0], head, raising=False)
            # Walk/attach
            cur = head
            for seg in parts[1:-1]:
                nxt = getattr(cur, seg, None)
                if nxt is None:
                    nxt = types.SimpleNamespace()
                    setattr(cur, seg, nxt)
                cur = nxt
            setattr(cur, parts[-1], fn)


def _mk_candidate() -> Dict[str, Any]:
    """
    Return a minimal candidate block-like object the orchestrator would submit.
    Keep it deliberately simple to avoid coupling to core types.
    """
    return {
        "header": {
            "height": 1,
            "parentHash": "0x00" * 16,
            "nonce": 1,
            "mixSeed": "0x11" * 16,
        },
        "txs": [],
        "proofs": [
            {"type": "ai", "psi": 0.55, "metrics": {"psi": 0.55}, "id": "ai:demo"},
            {"type": "quantum", "psi": 0.85, "metrics": {"psi": 0.85}, "id": "q:demo"},
            # HashShare to actually seal the block (abstract, not validated here)
            {"type": "hash", "psi": 0.20, "metrics": {"psi": 0.20}, "id": "h:demo"},
        ],
        "meta": {"devnet": True},
    }


@pytest.mark.skipif(_import_orchestrator() is None, reason="mining.orchestrator module not available")
def test_end_to_end_mine_one_block_with_ai_and_quantum(monkeypatch):
    """
    End-to-end (single node, fast-path mock):
      - Fake header template + packer ensures AI+Quantum proofs are attached
      - Orchestrator submits a block once
      - We assert the submitted candidate includes both AI and Quantum proofs
    This test is intentionally flexible and patches multiple probable hook points so
    different orchestrator implementations can pass without brittle coupling.
    """
    orch_mod = _import_orchestrator()
    entry = _find_entrypoint(orch_mod)
    if not entry:
        pytest.skip("No recognizable orchestrator entrypoint found")

    submitted: List[Dict[str, Any]] = []

    # ---- Fakes / fast path hooks ------------------------------------------------

    def fake_build_template(*args, **kwargs):
        # Minimal template the orchestrator may request
        return {"height": 1, "mixSeed": b"\x11" * 32, "nonceDomain": b"\x22" * 32}

    def fake_refresh_template(*args, **kwargs):
        return fake_build_template()

    def fake_pack_candidate(*args, **kwargs):
        # Ignore inputs; return a fixed candidate that contains AI + Quantum + HashShare
        return _mk_candidate()

    def fake_select_proofs(candidates, *args, **kwargs):
        # Prefer the provided AI + Quantum + one HashShare
        # If "candidates" is a list of dicts, keep the top by provided psi
        if isinstance(candidates, list) and candidates:
            # fall back to built candidate if needed
            base = {p["id"]: p for p in _mk_candidate()["proofs"]}
            chosen = []
            # Try to find ai/quantum/hash in candidates
            for want in ("ai", "quantum", "hash"):
                best = max(
                    (c for c in candidates if (c.get("type") or c.get("kind")) == want),
                    key=lambda x: float(x.get("psi") or x.get("metrics", {}).get("psi", 0.0)),
                    default=None,
                )
                if best:
                    chosen.append(best)
                elif want in base:
                    chosen.append(base[want])
            return chosen
        return _mk_candidate()["proofs"]

    def fake_submit_block(candidate: Dict[str, Any], *args, **kwargs):
        submitted.append(candidate)
        # Simulate node accept
        return {"ok": True, "height": candidate.get("header", {}).get("height", 1)}

    def fake_submit_share(*args, **kwargs):
        # No-op for this test; orchestrator might submit shares before full blocks.
        return {"ok": True}

    # Optional: neutralize sleeps/backoff in orchestrator loops
    try:
        import time

        monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None, raising=False)
    except Exception:
        pass

    # ---- Patch multiple likely integration points ------------------------------
    _patch_many(
        monkeypatch,
        orch_mod,
        {
            # template build/refresh
            "templates.build_template": fake_build_template,
            "templates.refresh": fake_refresh_template,
            "build_template": fake_build_template,
            "refresh_template": fake_refresh_template,
            # packer & selector
            "header_packer.pack_candidate": fake_pack_candidate,
            "pack_candidate": fake_pack_candidate,
            "proof_selector.select_proofs": fake_select_proofs,
            "select_proofs": fake_select_proofs,
            # submission paths
            "share_submitter.submit_block": fake_submit_block,
            "submit_block": fake_submit_block,
            "share_submitter.submit_share": fake_submit_share,
            "submit_share": fake_submit_share,
        },
    )

    # Some orchestrators route through adapters; provide simple shells
    for adapter_name, symbol, fn in [
        ("adapters.core_chain", "submit_block", fake_submit_block),
        ("adapters.core_chain", "get_head", lambda *a, **k: {"height": 0, "hash": "0xgenesis"}),
        ("adapters.consensus_view", "get_live_thresholds", lambda *a, **k: {"Theta": 1.0, "Gamma_cap": 2.0}),
        ("adapters.proofs_view", "verify_and_score", lambda proofs, *a, **k: proofs),
        ("adapters.aicf_queue", "poll_ready", lambda *a, **k: []),
    ]:
        ns = getattr(orch_mod, adapter_name.split(".")[0], None)
        if ns is None:
            ns = types.SimpleNamespace()
            setattr(orch_mod, adapter_name.split(".")[0], ns)
        cur = ns
        for seg in adapter_name.split(".")[1:]:
            nxt = getattr(cur, seg, None)
            if nxt is None:
                nxt = types.SimpleNamespace()
                setattr(cur, seg, nxt)
            cur = nxt
        setattr(cur, symbol, fn)

    # ---- Drive the orchestrator exactly once -----------------------------------
    if entry["kind"] == "fn":
        fn = entry["callable"]
        # Try best-effort call: run_once(config=..., limit=1, etc.)
        sig = inspect.signature(fn)
        kwargs = {}
        for p in sig.parameters.values():
            if p.name in ("limit", "max_blocks", "once", "iterations"):
                kwargs[p.name] = 1
            if p.name in ("config", "cfg", "options"):
                kwargs[p.name] = {"device": "cpu", "threads": 1, "devnet": True}
        fn(**kwargs)
    else:
        cls = entry["class"]
        method = entry["method"]
        # Construct with best-effort kwargs
        ctor_sig = inspect.signature(cls)
        ctor_kwargs = {}
        for p in ctor_sig.parameters.values():
            if p.name in ("config", "cfg", "options"):
                ctor_kwargs[p.name] = {"device": "cpu", "threads": 1, "devnet": True}
        obj = cls(**ctor_kwargs)  # type: ignore[arg-type]
        # Call run method once
        run = getattr(obj, method)
        run_sig = inspect.signature(run)
        call_kwargs = {}
        for p in run_sig.parameters.values():
            if p.name in ("limit", "max_blocks", "once", "iterations"):
                call_kwargs[p.name] = 1
        run(**call_kwargs)

        # Some implementations use start/stop; try to stop gracefully if present.
        for m in ("stop", "shutdown", "close"):
            if hasattr(obj, m) and callable(getattr(obj, m)):
                try:
                    getattr(obj, m)()
                except Exception:
                    pass

    # ---- Assertions ------------------------------------------------------------
    assert submitted, "No block candidate was submitted by the orchestrator"
    cand = submitted[-1]
    proofs = cand.get("proofs", [])
    kinds = {p.get("type") or p.get("kind") for p in proofs}
    assert "ai" in kinds, "Submitted block must include an AI proof"
    assert "quantum" in kinds, "Submitted block must include a Quantum proof"
    # Ensure there's at least one hash share to actually seal
    assert "hash" in kinds, "Submitted block should include a HashShare for sealing"
    # Total ψ should be > 0 (informal sanity)
    total_psi = 0.0
    for p in proofs:
        total_psi += float(p.get("psi") or p.get("metrics", {}).get("psi", 0.0))
    assert total_psi > 0.0

