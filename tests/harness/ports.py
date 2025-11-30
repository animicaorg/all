"""
tests.harness.ports — ephemeral port allocator for test services
================================================================

Provides a cross-platform, race-resistant way to pick TCP/UDP ports for
integration tests that spawn local services (RPC, WS, REST, etc.).

Key features
------------
- Safe reservation via a *bound socket* (default) to minimize race windows.
- Shared JSON registry with an atomic directory lock to avoid collisions
  across parallel pytest workers or separate test processes.
- xdist-aware port ranges so workers don't step on each other.
- Utilities to probe availability and wait for listeners.

Typical usage
-------------
    from tests.harness.ports import reserve_port, wait_for_listen

    # Reserve a port and keep it bound until we're ready to start the server
    with reserve_port(tag="rpc") as res:
        port = res.port
        proc = start_my_service("--port", str(port))
        # hand off the reservation to the server (closes the bound socket)
        res.handoff()
        assert wait_for_listen("127.0.0.1", port, timeout=10.0)

    # Or, grab a one-off OS-ephemeral free port (racey, but handy)
    port = choose_free_port()

Environment knobs
-----------------
TEST_PORT_START   : base start of the global allocation range (default: 47000)
TEST_PORT_SPAN    : size of the global allocation range (default: 6000)
TEST_PORT_REG_DIR : directory for the registry/lock (default: OS temp dir/animica-test-ports)
PYTEST_XDIST_WORKER : if present (e.g. "gw3"), each worker gets a disjoint sub-range.
"""

from __future__ import annotations

import contextlib
import errno
import ipaddress
import json
import os
import random
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

__all__ = [
    "reserve_port",
    "choose_free_port",
    "is_free",
    "wait_for_listen",
    "wait_for_closed",
    "port_range_for_worker",
]

# ---------------------------------------------------------------------------
# Locking: atomic directory lock (portable, robust against process crashes)
# ---------------------------------------------------------------------------


class DirLock:
    """
    Simple cross-platform directory lock.

    Creates a <path>.lock directory atomically. If it already exists,
    waits until it disappears or becomes stale (then breaks it).
    """

    def __init__(
        self,
        path: Path,
        timeout: float = 15.0,
        poll: float = 0.05,
        stale_sec: float = 120.0,
    ) -> None:
        self.base = Path(path)
        self.lock_dir = (
            self.base.with_suffix(self.base.suffix + ".lock")
            if self.base.suffix
            else Path(str(self.base) + ".lock")
        )
        self.timeout = timeout
        self.poll = poll
        self.stale_sec = stale_sec

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            try:
                self.lock_dir.mkdir(parents=True, exist_ok=False)
                # Write owner marker (pid + time)
                with open(self.lock_dir / "owner.json", "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(), "ts": time.time()}, f)
                return self
            except FileExistsError:
                # If stale, break it
                owner = self.lock_dir / "owner.json"
                if owner.exists():
                    try:
                        meta = json.loads(owner.read_text(encoding="utf-8"))
                        if time.time() - float(meta.get("ts", 0.0)) > self.stale_sec:
                            # Stale lock: best-effort cleanup
                            with contextlib.suppress(Exception):
                                owner.unlink()
                            with contextlib.suppress(Exception):
                                self.lock_dir.rmdir()
                            continue
                    except Exception:
                        # Corrupt owner file; consider stale after window
                        if time.time() - self.lock_dir.stat().st_mtime > self.stale_sec:
                            with contextlib.suppress(Exception):
                                for p in self.lock_dir.iterdir():
                                    with contextlib.suppress(Exception):
                                        p.unlink()
                                self.lock_dir.rmdir()
                            continue
                if time.time() > deadline:
                    raise TimeoutError(f"Timeout acquiring lock: {self.lock_dir}")
                time.sleep(self.poll)

    def __exit__(self, exc_type, exc, tb):
        # Best-effort unlock
        with contextlib.suppress(Exception):
            for p in self.lock_dir.iterdir():
                with contextlib.suppress(Exception):
                    p.unlink()
            self.lock_dir.rmdir()


# ---------------------------------------------------------------------------
# Registry & allocation
# ---------------------------------------------------------------------------

REG_FILE = "registry.json"


def _registry_dir() -> Path:
    root = os.getenv("TEST_PORT_REG_DIR") or os.path.join(
        tempfile.gettempdir(), "animica-test-ports"
    )
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _registry_path() -> Path:
    return _registry_dir() / REG_FILE


def _load_registry() -> Dict[str, Dict[str, float]]:
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Normalize keys as strings
        return {str(k): v for k, v in data.items()}
    except Exception:
        return {}


def _save_registry(reg: Dict[str, Dict[str, float]]) -> None:
    path = _registry_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def port_range_for_worker() -> Tuple[int, int]:
    """
    Determine the allocation range, optionally segmented by xdist worker.

    Global range: [START, START+SPAN-1]  (defaults 47000..52999)
    If PYTEST_XDIST_WORKER is set, split into 10 equal buckets by worker index.
    """
    start = int(os.getenv("TEST_PORT_START", "47000"))
    span = int(os.getenv("TEST_PORT_SPAN", "6000"))
    end = start + span - 1

    worker = os.getenv("PYTEST_XDIST_WORKER")  # e.g., "gw0", "gw2"
    if worker:
        # Extract trailing digits
        digits = "".join(ch for ch in worker if ch.isdigit())
        idx = int(digits) if digits else 0
        buckets = 10
        bucket_span = max(1, span // buckets)
        w_start = start + (idx % buckets) * bucket_span
        w_end = min(end, w_start + bucket_span - 1)
        return w_start, w_end
    return start, end


def _inet_family_for(host: str):
    try:
        ip = ipaddress.ip_address(host)
        return socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    except ValueError:
        # Resolve hostname best-effort
        try:
            info = socket.getaddrinfo(host, None, 0, 0, 0, socket.AI_ADDRCONFIG)
            return info[0][0] if info else socket.AF_INET
        except Exception:
            return socket.AF_INET


def _try_bind(host: str, port: int) -> Optional[socket.socket]:
    """Attempt to bind a TCP socket to (host, port). On success, return the bound socket (still open)."""
    family = _inet_family_for(host)
    s = socket.socket(family, socket.SOCK_STREAM)
    # Reuseaddr is safe for quick tests; we still rely on bound ownership until handoff.
    with contextlib.suppress(Exception):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return s
    except OSError:
        s.close()
        return None


def _probe_free(host: str, port: int) -> bool:
    s = _try_bind(host, port)
    if s is None:
        return False
    s.close()
    return True


def _purge_stale(
    reg: Dict[str, Dict[str, float]], host: str, stale_sec: float = 120.0
) -> None:
    """Drop entries older than stale_sec *and* currently free."""
    now = time.time()
    to_del = []
    for p_str, meta in reg.items():
        ts = float(meta.get("ts", 0.0))
        if now - ts > stale_sec:
            # Check actual availability before deleting
            if _probe_free(host, int(p_str)):
                to_del.append(p_str)
    for k in to_del:
        reg.pop(k, None)


@dataclass
class Reservation:
    host: str
    port: int
    tag: Optional[str]
    _sock: Optional[socket.socket]
    _released: bool = False

    def handoff(self) -> None:
        """
        Release the bound socket so the actual service can bind to the port.
        Registry entry is kept until `release()` or context exit.
        """
        if self._sock is not None:
            with contextlib.suppress(Exception):
                self._sock.close()
            self._sock = None

    def release(self) -> None:
        """Remove from registry and close any held socket."""
        if self._released:
            return
        self._released = True
        self.handoff()
        # Remove from registry under lock
        reg_base = _registry_dir()
        with DirLock(reg_base):
            reg = _load_registry()
            reg.pop(str(self.port), None)
            _save_registry(reg)

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()

    def __del__(self):
        # Emergency cleanup; avoid raising in GC
        with contextlib.suppress(Exception):
            self.release()


def reserve_port(
    tag: Optional[str] = None,
    host: str = "127.0.0.1",
    keep_bound: bool = True,
    jitter: bool = True,
) -> Reservation:
    """
    Reserve a TCP port from the per-worker range.

    - When keep_bound=True (default), the returned Reservation keeps a socket
      *bound* to the port until you call `handoff()` (or exit the context),
      minimizing allocation races.
    - A shared on-disk registry prevents parallel processes from choosing the
      same port.

    Parameters
    ----------
    tag : Optional[str]
        Free-form label for debugging (e.g., "rpc", "ws", "services").
    host : str
        Host interface to test-bind against (default "127.0.0.1").
    keep_bound : bool
        Keep a socket bound to the port until handoff/release.
    jitter : bool
        Shuffle the scan order within the range for better distribution.

    Raises
    ------
    RuntimeError if no free port is found in the range.
    """
    start, end = port_range_for_worker()
    candidates = list(range(start, end + 1))
    if jitter:
        random.shuffle(candidates)

    reg_base = _registry_dir()
    with DirLock(reg_base):
        reg = _load_registry()
        _purge_stale(reg, host)

        for p in candidates:
            key = str(p)
            if key in reg:
                continue
            sock = _try_bind(host, p)
            if sock is None:
                continue
            # Mark in registry
            reg[key] = {"pid": float(os.getpid()), "ts": time.time(), "tag": tag or ""}
            _save_registry(reg)

            # If we don't want to hold the bind, close now.
            if not keep_bound:
                with contextlib.suppress(Exception):
                    sock.close()
                sock = None

            return Reservation(host=host, port=p, tag=tag, _sock=sock)

    raise RuntimeError(f"No free ports found in range {start}-{end} for host {host}")


def choose_free_port(host: str = "127.0.0.1") -> int:
    """
    Ask the OS for an ephemeral free port by binding to port 0, then close it.

    Note: inherently racey; prefer `reserve_port()` for critical paths.
    """
    family = _inet_family_for(host)
    with contextlib.closing(socket.socket(family, socket.SOCK_STREAM)) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def is_free(host: str, port: int, timeout: float = 0.25) -> bool:
    """Return True if we can bind to (host, port) *or* connect times out/refused."""
    if _probe_free(host, port):
        return True
    # If we can't bind, it might be because something is listening already.
    # Try a quick connect; if succeeds, it's not free.
    try:
        with contextlib.closing(
            socket.create_connection((host, port), timeout=timeout)
        ):
            return False
    except OSError as e:
        # Connection refused/timeout -> treat as free (nothing listening)
        if e.errno in (errno.ECONNREFUSED, errno.ETIMEDOUT) or isinstance(
            e, TimeoutError
        ):
            return True
        return False


def wait_for_listen(
    host: str, port: int, timeout: float = 10.0, interval: float = 0.05
) -> bool:
    """
    Wait until a TCP server is listening on (host, port). Returns True if ready.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with contextlib.closing(
                socket.create_connection((host, port), timeout=interval)
            ):
                return True
        except OSError:
            time.sleep(interval)
    return False


def wait_for_closed(
    host: str, port: int, timeout: float = 10.0, interval: float = 0.05
) -> bool:
    """
    Wait until nothing is listening on (host, port). Returns True when closed.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with contextlib.closing(
                socket.create_connection((host, port), timeout=interval)
            ):
                # Still open
                time.sleep(interval)
        except OSError:
            return True
    return False
