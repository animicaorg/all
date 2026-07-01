"""Node-local persistence for the Bitcoin-compat wallet facade.

Animica is account-model with **no node wallet registry** and **no
address-indexed transaction history**. Bitcoin-Core wallet integrations
(exchanges) expect both: a wallet that sums many addresses (``getbalance``,
``listunspent`` with no args) and a way to *detect* incoming deposits
(``listsinceblock`` / ``listtransactions`` / ``getreceivedbyaddress``).

This module is the missing piece — a tiny, defensive, node-local store that:

  * remembers the set of **watched addresses** (added explicitly via
    ``importaddress`` *or* implicitly the first time any wallet RPC is asked
    about an address), and
  * remembers the **last balance we observed** for each, so a periodic poll
    can turn *balance increases* into synthetic "receive" events with stable,
    unique, non-zero txids.

It is a pure RPC-layer convenience. It NEVER touches consensus, state, block
validation, or the mempool — it only *reads* balances through the registered
``state.getBalance`` native and records what it saw. Every operation is wrapped
so a storage hiccup can never propagate into (and break) the JSON-RPC server.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_SCHEMA_VERSION = 1
_MAX_EVENTS = 20000          # ring-buffer cap for synthetic deposit events
_LOCK = threading.RLock()
_STATE: Optional[Dict[str, Any]] = None
_PATH: Optional[Path] = None


# --------------------------------------------------------------------------- #
# Path resolution — prefer a durable, node-writable location.                 #
# --------------------------------------------------------------------------- #
def _candidate_paths() -> List[Path]:
    out: List[Path] = []
    env_file = os.environ.get("ANIMICA_BTC_COMPAT_WALLET_FILE")
    if env_file:
        out.append(Path(env_file))
    data_dir = os.environ.get("ANIMICA_DATA_DIR") or os.environ.get("ANIMICA_HOME")
    if data_dir:
        out.append(Path(data_dir) / "btc_compat_wallet.json")
    # In the mainnet docker node /data is a bind-mounted volume that survives
    # `docker restart`, so it is the ideal home for the watch-list.
    out.append(Path("/data") / "btc_compat_wallet.json")
    out.append(Path.home() / ".animica" / "btc_compat_wallet.json")
    out.append(Path(tempfile.gettempdir()) / "animica_btc_compat_wallet.json")
    return out


def _resolve_path() -> Path:
    global _PATH
    if _PATH is not None:
        return _PATH
    for cand in _candidate_paths():
        try:
            cand.parent.mkdir(parents=True, exist_ok=True)
            # Confirm the directory is writable by touching the target.
            probe = cand.parent / (".w_" + cand.name)
            with open(probe, "w") as fh:
                fh.write("")
            probe.unlink(missing_ok=True)
            _PATH = cand
            return _PATH
        except Exception:
            continue
    # Last resort: an in-process temp path (may not persist, but never crashes).
    _PATH = Path(tempfile.gettempdir()) / "animica_btc_compat_wallet.json"
    return _PATH


def _empty_state() -> Dict[str, Any]:
    return {"version": _SCHEMA_VERSION, "addresses": {}, "events": []}


def _load() -> Dict[str, Any]:
    global _STATE
    if _STATE is not None:
        return _STATE
    path = _resolve_path()
    try:
        if path.exists():
            with open(path, "r") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("addresses"), dict):
                data.setdefault("events", [])
                data.setdefault("version", _SCHEMA_VERSION)
                _STATE = data
                return _STATE
    except Exception:
        pass
    _STATE = _empty_state()
    return _STATE


def _save() -> None:
    """Atomically persist. Best-effort: a write failure is swallowed."""
    path = _resolve_path()
    state = _load()
    try:
        # Trim the event ring buffer before writing.
        evs = state.get("events") or []
        if len(evs) > _MAX_EVENTS:
            state["events"] = evs[-_MAX_EVENTS:]
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".btcw_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(state, fh, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
    except Exception:
        # Persistence is a convenience; the in-memory state still works.
        pass


def _norm(addr: Any) -> str:
    return str(addr or "").strip()


def _synth_txid(addr: str, seq: int, amount_nanos: int, height: int) -> str:
    """A deterministic, unique, non-zero 32-byte id for a synthetic credit.

    Unique per (address, running received total) so the same deposit maps to
    the same txid across polls, and two different deposits never collide.
    """
    h = hashlib.sha256(f"{addr}|{seq}|{amount_nanos}|{height}".encode()).hexdigest()
    return h  # 64 hex chars, effectively never all-zero


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #
def watch(addr: Any, label: str = "", baseline_nanos: Optional[int] = None) -> None:
    """Register an address for wallet tracking (idempotent).

    ``baseline_nanos`` controls whether the address's *current* balance later
    shows up as a deposit:
      * ``None`` / ``0``  -> baseline 0, so the existing balance surfaces as a
        "receive" on the next poll (Bitcoin ``importaddress rescan=true``).
      * a positive int    -> baseline that amount, so only *new* funds beyond it
        are reported (``importaddress rescan=false``).
    """
    a = _norm(addr)
    if not a:
        return
    with _LOCK:
        state = _load()
        rec = state["addresses"].get(a)
        if rec is None:
            state["addresses"][a] = {
                "label": str(label or ""),
                "watch_ts": int(time.time()),
                "seen_balance": int(baseline_nanos or 0),
                "received_total": 0,
                "detect_height": 0,
                "seq": 0,
            }
            _save()
        elif label and not rec.get("label"):
            rec["label"] = str(label)
            _save()


def is_watched(addr: Any) -> bool:
    with _LOCK:
        return _norm(addr) in _load()["addresses"]


def addresses(label: Optional[str] = None) -> List[str]:
    """All watched addresses, optionally filtered by exact label."""
    with _LOCK:
        recs = _load()["addresses"]
        if label is None:
            return list(recs.keys())
        return [a for a, r in recs.items() if str(r.get("label", "")) == str(label)]


def label_of(addr: Any) -> str:
    with _LOCK:
        return str(_load()["addresses"].get(_norm(addr), {}).get("label", ""))


def received(addr: Any) -> int:
    """Best-effort cumulative received (nANM) for a watched address."""
    with _LOCK:
        rec = _load()["addresses"].get(_norm(addr))
        if not rec:
            return 0
        return max(int(rec.get("received_total", 0)), int(rec.get("seen_balance", 0)))


def poll(balance_fn: Callable[[str], int], head_height: int,
         only: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Reconcile watched balances against chain state; emit receive events.

    ``balance_fn`` maps an address -> its current balance in nANM (raises or
    returns a non-int on failure, which we skip). Returns the list of *new*
    synthetic receive events produced by this poll (also appended to the store).

    NOTE (limitation): detection is delta-based. If a deposit *and* a spend net
    out between two polls the intermediate credit is not observed — exchanges
    should poll frequently. ``detect_height`` is when we first *observed* the
    credit, a lower bound on the deposit's true height, so confirmations derived
    from it never over-state.

    Concurrency: balances are read WITHOUT holding the store lock (the reads hit
    the state DB and can be slow); only the snapshot + apply steps are locked.
    """
    now = int(time.time())
    hh = 0
    try:
        hh = int(head_height)
    except Exception:
        hh = 0
    if hh <= 0:
        # A transient chain.getHead failure would record detect_height=0 and
        # freeze the deposit's confirmations — skip this round, retry next poll.
        return []

    # 1) Snapshot targets under the lock (fast).
    with _LOCK:
        state = _load()
        targets = list(only) if only is not None else list(state["addresses"].keys())
        targets = [a for a in targets if a in state["addresses"]]

    # 2) Read current balances WITHOUT the lock (potentially slow I/O).
    curr: Dict[str, int] = {}
    for a in targets:
        try:
            curr[a] = int(balance_fn(a))
        except Exception:
            continue

    # 3) Apply deltas under the lock (fast), re-reading seen_balance to avoid races.
    new_events: List[Dict[str, Any]] = []
    with _LOCK:
        state = _load()
        changed = False
        for a, cur in curr.items():
            rec = state["addresses"].get(a)
            if rec is None:
                continue
            prev = int(rec.get("seen_balance", 0))
            if cur > prev:
                delta = cur - prev
                rec["seq"] = int(rec.get("seq", 0)) + 1
                rec["received_total"] = int(rec.get("received_total", 0)) + delta
                rec["detect_height"] = hh
                txid = _synth_txid(a, rec["seq"], delta, hh)
                ev = {
                    "txid": txid,
                    "address": a,
                    "label": str(rec.get("label", "")),
                    "category": "receive",
                    "amount_nanos": delta,
                    "detect_height": hh,
                    "time": now,
                }
                state["events"].append(ev)
                new_events.append(ev)
                rec["seen_balance"] = cur
                changed = True
            elif cur < prev:
                # A spend/sweep reduced the balance — track it, no receive event.
                rec["seen_balance"] = cur
                changed = True
        if changed:
            _save()
    return new_events


def events(limit: int = 100, address: Optional[str] = None,
           label: Optional[str] = None, min_height: Optional[int] = None) -> List[Dict[str, Any]]:
    """Recent synthetic deposit events, newest last (Bitcoin listtransactions order)."""
    with _LOCK:
        evs = list(_load().get("events") or [])
    a = _norm(address) if address else None
    out = []
    for ev in evs:
        if a and ev.get("address") != a:
            continue
        if label is not None and str(ev.get("label", "")) != str(label):
            continue
        if min_height is not None and int(ev.get("detect_height", 0)) < int(min_height):
            continue
        out.append(ev)
    if limit and len(out) > limit:
        out = out[-limit:]
    return out


def reset_for_tests() -> None:
    """Test hook: drop in-memory + on-disk state."""
    global _STATE, _PATH
    with _LOCK:
        _STATE = _empty_state()
        try:
            if _PATH and _PATH.exists():
                _PATH.unlink()
        except Exception:
            pass
