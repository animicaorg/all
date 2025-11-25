# -*- coding: utf-8 -*-
"""
Atheris fuzz target: **Randomness commit/reveal edge cases & timing windows**

What this exercises
-------------------
- Builds a commit C from (address, salt, payload), then attempts a reveal.
- Tries project helpers (randomness.commit_reveal.commit / verify / round_manager),
  falling back to a local deterministic model if unavailable.
- Perturbs:
    * salt / payload mismatches
    * early/late reveal outside window
    * commit outside window
- Ensures (best-effort) that valid → mutated becomes invalid, and that timing gates
  are respected under the fallback timing model.

Run via shared harness:
  python tests/fuzz/atheris_runner.py \
    --target tests.fuzz.fuzz_randomness_inputs:fuzz \
    tests/fuzz/corpus_txs  # any seed dir; dedicated corpus recommended

Or directly:
  python -m tests.fuzz.fuzz_randomness_inputs tests/fuzz/corpus_txs
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Optional, Tuple, Dict


# ---------------- optional import helper ----------------

def _import_optional(modname: str):
    try:
        __import__(modname)
        return sys.modules[modname]
    except Exception:
        return None


# ---------------- hashing ----------------

def _sha3_256(data: bytes) -> bytes:
    # Prefer project hash wrappers if present
    for modname, fn in (("randomness.utils.hash", "sha3_256"),
                        ("core.utils.hash", "sha3_256"),
                        ("da.utils.hash", "sha3_256")):
        m = _import_optional(modname)
        if m and hasattr(m, fn):
            try:
                return getattr(m, fn)(data)  # type: ignore
            except Exception:
                pass
    import hashlib
    return hashlib.sha3_256(data).digest()


# ---------------- project adapters (best-effort) ----------------

_commit_mod = _import_optional("randomness.commit_reveal.commit")
_verify_mod = _import_optional("randomness.commit_reveal.verify")
_round_mgr  = _import_optional("randomness.commit_reveal.round_manager")
_beacon_sched = _import_optional("randomness.beacon.schedule")

def _build_commitment_project(addr: bytes, salt: bytes, payload: bytes) -> Optional[Any]:
    if not _commit_mod:
        return None
    # Try several plausible signatures
    for args, kwargs in (
        ((addr, salt, payload), {}),
        ((), {"address": addr, "salt": salt, "payload": payload}),
        ((), {"addr": addr, "salt": salt, "payload": payload}),
    ):
        try:
            c = getattr(_commit_mod, "build_commitment", None) or getattr(_commit_mod, "commit", None)
            if callable(c):
                return c(*args, **kwargs)
        except Exception:
            continue
    return None


def _extract_commit_bytes(commit_obj: Any) -> Optional[bytes]:
    if isinstance(commit_obj, (bytes, bytearray, memoryview)):
        return bytes(commit_obj)
    if isinstance(commit_obj, dict):
        for k in ("commitment", "C", "value", "digest", "hash"):
            v = commit_obj.get(k)
            if isinstance(v, (bytes, bytearray, memoryview)):
                return bytes(v)
            if isinstance(v, str):
                # accept hex-ish strings
                try:
                    if v.startswith("0x"):
                        return bytes.fromhex(v[2:])
                    return bytes.fromhex(v)
                except Exception:
                    pass
    return None


def _project_verify(commitment: Any, addr: bytes, salt: bytes, payload: bytes, now_ts: int, sched: Dict[str, int]) -> Optional[bool]:
    if not _verify_mod:
        return None

    reveal_obj = {"address": addr, "salt": salt, "payload": payload, "time": now_ts}
    # Known knobs we might pass along
    attempts: list[tuple[tuple, dict]] = [
        ((commitment, reveal_obj, now_ts, sched), {}),
        ((), {"commitment": commitment, "reveal": reveal_obj, "now": now_ts, "schedule": sched}),
        ((commitment, addr, salt, payload, now_ts, sched), {}),
        ((commitment, addr, salt, payload, now_ts), {}),
        ((commitment, reveal_obj), {}),
    ]
    for fn_name in ("verify_reveal", "verify", "check_reveal", "validate_reveal"):
        fn = getattr(_verify_mod, fn_name, None)
        if not callable(fn):
            continue
        for args, kwargs in attempts:
            try:
                res = fn(*args, **kwargs)
                if isinstance(res, bool):
                    return res
                if isinstance(res, dict):
                    # Common shape: {"ok": True}
                    for k in ("ok", "valid", "verified", "is_valid"):
                        v = res.get(k)
                        if isinstance(v, bool):
                            return v
            except (TypeError, ValueError, AssertionError):
                continue
            except Exception:
                # Don't crash fuzz loop on unexpected project exceptions
                continue
    return None


def _get_schedule_project(commit_len: int, reveal_len: int, settle_len: int) -> Optional[Dict[str, int]]:
    # Ask round_manager / beacon.schedule, else None
    # We provide the numbers we want to test as hints when possible; many APIs
    # compute from chain params instead, which is also fine.
    if _round_mgr and hasattr(_round_mgr, "current_schedule"):
        try:
            sch = _round_mgr.current_schedule()  # type: ignore
            if isinstance(sch, dict):
                c = int(sch.get("commit", commit_len))
                r = int(sch.get("reveal", reveal_len))
                s = int(sch.get("settle", settle_len))
                t = int(sch.get("total", c + r + s))
                return {"commit": c, "reveal": r, "settle": s, "total": t}
        except Exception:
            pass
    if _beacon_sched and hasattr(_beacon_sched, "schedule"):
        try:
            sch = _beacon_sched.schedule()  # type: ignore
            if isinstance(sch, dict):
                c = int(sch.get("commit", commit_len))
                r = int(sch.get("reveal", reveal_len))
                s = int(sch.get("settle", settle_len))
                t = int(sch.get("total", c + r + s))
                return {"commit": c, "reveal": r, "settle": s, "total": t}
        except Exception:
            pass
    return None


# ---------------- fallback timing & verify model ----------------

def _fallback_commit(addr: bytes, salt: bytes, payload: bytes) -> bytes:
    return _sha3_256(b"commit|" + addr + b"|" + salt + b"|" + payload)

def _fallback_verify(commit_bytes: bytes, addr: bytes, salt: bytes, payload: bytes, t_commit: int, t_reveal: int, sched: Dict[str, int]) -> bool:
    c = _fallback_commit(addr, salt, payload)
    if c != commit_bytes:
        return False
    commit_len = int(sched["commit"])
    reveal_len = int(sched["reveal"])
    total = int(sched["total"])
    # Commit must be within [0, commit_len)
    if not (0 <= t_commit < commit_len):
        return False
    # Reveal must be in [commit_len, commit_len+reveal_len)
    if not (commit_len <= t_reveal < commit_len + reveal_len):
        return False
    # Sanity: both within round bounds
    if t_reveal >= total:
        return False
    # Monotonic: reveal after commit time
    if t_reveal < t_commit:
        return False
    return True


# ---------------- byte cursor for shaping fuzz data ----------------

class Cur:
    def __init__(self, b: bytes):
        self.b = b
        self.i = 0

    def u8(self) -> int:
        if self.i >= len(self.b):
            return 0
        v = self.b[self.i]
        self.i += 1
        return v

    def take(self, n: int) -> bytes:
        if n <= 0:
            return b""
        if self.i >= len(self.b):
            return b"\x00" * n
        j = min(self.i + n, len(self.b))
        out = self.b[self.i:j]
        self.i = j
        if len(out) < n:
            out = out + b"\x00" * (n - len(out))
        return out


# ---------------- main fuzz logic ----------------

def fuzz(data: bytes) -> None:
    # Size guard
    if len(data) > (1 << 20):
        return

    cur = Cur(data)

    # Schedule (seconds)
    commit_len = 1 + (cur.u8() % 60)
    reveal_len = 1 + (cur.u8() % 60)
    settle_len = (cur.u8() % 60)
    total = commit_len + reveal_len + settle_len

    sched = _get_schedule_project(commit_len, reveal_len, settle_len) or {
        "commit": commit_len,
        "reveal": reveal_len,
        "settle": settle_len,
        "total": total,
    }

    # Times relative to start of round
    t_commit = cur.u8() % (total + 20)   # may intentionally go out of window
    t_reveal = cur.u8() % (total + 20)

    # Inputs
    addr = _sha3_256(b"addr|" + data)[:20]  # 20-byte address derived deterministically
    salt_len = cur.u8() % 32
    pay_len  = cur.u8() % 64
    salt = cur.take(salt_len)
    payload = cur.take(pay_len)

    # Build commitment (project or fallback)
    commit_obj = _build_commitment_project(addr, salt, payload)
    commit_bytes = _extract_commit_bytes(commit_obj) if commit_obj is not None else None
    if commit_bytes is None:
        commit_bytes = _fallback_commit(addr, salt, payload)

    # Decide whether to ask project verify; if not, use fallback model
    proj_ok: Optional[bool] = _project_verify(commit_obj if commit_obj is not None else commit_bytes,
                                              addr, salt, payload, t_reveal, sched)

    if proj_ok is None:
        # Fallback truth value
        base_ok = _fallback_verify(commit_bytes, addr, salt, payload, t_commit, t_reveal, sched)
    else:
        base_ok = proj_ok

    # --- Mutations ---
    flip_salt = (cur.u8() & 1) == 1
    flip_pay  = (cur.u8() & 1) == 1
    shift_kind = cur.u8() % 3  # 0: early, 1: late, 2: move-to-reveal-start
    shift_amt  = 1 + (cur.u8() % 8)

    bad_salt = (salt[:-1] + bytes([salt[-1] ^ 0x01])) if salt else b"\x01"
    bad_pay  = (payload[:-1] + bytes([payload[-1] ^ 0x01])) if payload else b"\x02"

    # Time shifts
    if shift_kind == 0:
        t_reveal_bad = max(0, t_reveal - shift_amt)  # likely too early
    elif shift_kind == 1:
        t_reveal_bad = t_reveal + shift_amt          # likely too late
    else:
        t_reveal_bad = sched["commit"]               # edge: exactly reveal start

    # Compose mutated reveals
    salt2 = bad_salt if flip_salt else salt
    pay2  = bad_pay if flip_pay else payload

    # Check mutated outcomes via project or fallback
    proj_mut_ok: Optional[bool] = _project_verify(commit_obj if commit_obj is not None else commit_bytes,
                                                  addr, salt2, pay2, t_reveal_bad, sched)

    if proj_mut_ok is None:
        mut_ok = _fallback_verify(commit_bytes, addr, salt2, pay2, t_commit, t_reveal_bad, sched)
    else:
        mut_ok = proj_mut_ok

    # --- Invariants (best-effort) ---
    # If base reveal was accepted, mutated should not remain accepted when we:
    #  - changed salt or payload (binding)
    #  - shifted time outside reveal window (timing)
    try:
        if base_ok:
            # Changing binding should fail
            if (flip_salt or flip_pay) and mut_ok:
                raise AssertionError("Reveal accepted despite salt/payload mutation")
            # Timing: if we definitely shoved outside window under fallback math, enforce.
            c_len, r_len = sched["commit"], sched["reveal"]
            in_window = (c_len <= t_reveal < c_len + r_len)
            in_window_bad = (c_len <= t_reveal_bad < c_len + r_len)
            if in_window and not in_window_bad and mut_ok:
                raise AssertionError("Reveal accepted outside window after time shift")
    except Exception:
        # Bubble assertion to fuzzer (intentional)
        raise


# ---------------- direct execution ----------------

def _run_direct(argv: list[str]) -> int:  # pragma: no cover
    try:
        import atheris  # type: ignore
    except Exception:
        sys.stderr.write("[fuzz_randomness_inputs] atheris not installed. pip install atheris\n")
        return 2
    atheris.instrument_all()
    corpus = [p for p in argv if not p.startswith("-")] or ["tests/fuzz/corpus_txs"]
    atheris.Setup([sys.argv[0], *corpus], fuzz, enable_python_coverage=True)
    atheris.Fuzz()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_run_direct(sys.argv[1:]))
