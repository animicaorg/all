"""Node auto-updater.

Keeps a node on the latest published ``animica`` release with no manual action,
so a fix (like the chain-reset self-heal in this release) propagates across the
network as operators' nodes pick it up. Conservative by construction — a naive
"pip install latest + restart everywhere at once" is how a bad release bricks a
whole network, so this has guard rails:

  * Off unless explicitly enabled (``ANIMICA_AUTOUPDATE``).
  * Only ever moves to a strictly-NEWER semver than what's running.
  * Honors a minimum release age (default 6h) so a broken release isn't adopted
    network-wide within minutes of being pushed — there's time to yank it.
  * Jittered check interval so nodes don't all update on the same tick.
  * Never raises into the caller; any failure just skips the cycle.
  * Restart only when a supervisor is present (docker restart-policy / systemd
    ``Restart=always``); otherwise it installs and applies on the next restart.

Env:
  ANIMICA_AUTOUPDATE          off|0 (default) | notify | install | restart|1
  ANIMICA_AUTOUPDATE_INTERVAL_H   check cadence in hours (default 6)
  ANIMICA_AUTOUPDATE_MIN_AGE_H    min release age before adopting (default 6)
  ANIMICA_AUTOUPDATE_INDEX_URL    PyPI JSON base (default https://pypi.org/pypi)
  ANIMICA_AUTOUPDATE_CMD          override install command (e.g. a git-pull for
                                  source/bind-mount deploys); default is
                                  "<python> -m pip install -U animica"
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Optional, Tuple

log = logging.getLogger("animica.autoupdate")

PACKAGE = "animica"
_DISABLED = {"", "0", "off", "false", "no"}


def _mode() -> str:
    raw = (os.environ.get("ANIMICA_AUTOUPDATE") or "").strip().lower()
    if raw in _DISABLED:
        return "off"
    if raw in {"1", "true", "yes", "on"}:
        return "restart"
    if raw in {"notify", "install", "restart"}:
        return raw
    return "off"


def _current_version() -> str:
    try:
        from animica import __version__  # type: ignore

        return str(__version__)
    except Exception:
        try:
            from importlib.metadata import version

            return version(PACKAGE)
        except Exception:
            return "0.0.0"


def _parse(v: str) -> Tuple[int, ...]:
    # Compare release segments only; a pre-release ("1.9.13rc1") sorts BELOW the
    # final ("1.9.13") so we never auto-adopt a pre-release over a release.
    core = "".join(c if (c.isdigit() or c == ".") else " " for c in v).split()
    nums = core[0] if core else v
    parts = []
    for seg in nums.split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            break
    is_pre = any(t in v.lower() for t in ("rc", "a", "b", "dev", "pre"))
    return tuple(parts) + ((-1,) if is_pre else (0,))


def _is_newer(latest: str, current: str) -> bool:
    try:
        return _parse(latest) > _parse(current)
    except Exception:
        return False


def _latest(index_url: str, min_age_h: float) -> Optional[str]:
    """Latest release on the index that is at least ``min_age_h`` old."""
    url = f"{index_url.rstrip('/')}/{PACKAGE}/json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (trusted index)
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log.debug("autoupdate: index check failed: %s", exc)
        return None

    candidate = str((data.get("info") or {}).get("version") or "")
    if not candidate:
        return None

    # Enforce minimum age using the newest file's upload time for that version.
    try:
        files = (data.get("releases") or {}).get(candidate) or []
        uploaded = max(
            (f.get("upload_time_iso_8601") or f.get("upload_time") or "") for f in files
        ) if files else ""
        if uploaded and min_age_h > 0:
            from datetime import datetime, timezone

            ts = uploaded.replace("Z", "+00:00")
            up = datetime.fromisoformat(ts)
            if up.tzinfo is None:
                up = up.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - up).total_seconds() / 3600.0
            if age_h < min_age_h:
                log.info(
                    "autoupdate: %s is only %.1fh old (< %.1fh) — waiting",
                    candidate, age_h, min_age_h,
                )
                return None
    except Exception:
        pass
    return candidate


def _install(target: str) -> bool:
    cmd_override = os.environ.get("ANIMICA_AUTOUPDATE_CMD")
    if cmd_override:
        cmd = cmd_override.split()
    else:
        cmd = [sys.executable, "-m", "pip", "install", "-U", f"{PACKAGE}=={target}"]
    log.warning("autoupdate: installing %s via %s", target, " ".join(cmd))
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            log.error("autoupdate: install failed (%s): %s", res.returncode, res.stderr[-500:])
            return False
        return True
    except Exception as exc:
        log.error("autoupdate: install error: %s", exc)
        return False


def _has_supervisor() -> bool:
    # A supervisor will restart us after we exit. Detect the common ones.
    if os.environ.get("ANIMICA_SUPERVISED", "").strip().lower() in {"1", "true", "yes"}:
        return True
    if os.path.exists("/.dockerenv"):
        return True
    if os.environ.get("INVOCATION_ID"):  # systemd sets this per-unit
        return True
    return False


def _restart() -> None:
    log.warning("autoupdate: restarting to apply the update")
    if _has_supervisor():
        # Graceful: SIGTERM lets the node shut down cleanly (flush DB) and the
        # supervisor brings it back on the new version.
        os.kill(os.getpid(), signal.SIGTERM)
    else:
        log.warning(
            "autoupdate: no supervisor detected — update installed, will apply "
            "on the next manual restart (set ANIMICA_SUPERVISED=1 to auto-restart)"
        )


def run_once() -> Optional[str]:
    """Check once; act per mode. Returns the version updated to, else None."""
    mode = _mode()
    if mode == "off":
        return None
    index_url = os.environ.get("ANIMICA_AUTOUPDATE_INDEX_URL", "https://pypi.org/pypi")
    min_age = float(os.environ.get("ANIMICA_AUTOUPDATE_MIN_AGE_H", "6") or 6)
    current = _current_version()
    latest = _latest(index_url, min_age)
    if not latest or not _is_newer(latest, current):
        return None

    log.warning("autoupdate: newer release available: %s -> %s (mode=%s)", current, latest, mode)
    if mode == "notify":
        return None
    if not _install(latest):
        return None
    if mode == "restart":
        _restart()
    return latest


def _loop(interval_h: float) -> None:
    # Initial jittered delay (deterministic per-pid, no Math.random needed) so
    # nodes spread their checks across the window.
    base = max(0.25, interval_h) * 3600.0
    jitter = (os.getpid() % 100) / 100.0  # 0..1
    time.sleep(min(base, 60.0 + jitter * 300.0))
    while True:
        try:
            run_once()
        except Exception as exc:  # never let the updater take down the node
            log.debug("autoupdate: cycle error: %s", exc)
        time.sleep(base * (0.85 + 0.30 * jitter))


def start(interval_h: Optional[float] = None) -> Optional[threading.Thread]:
    """Start the background auto-updater if enabled. Safe to call once at boot."""
    if _mode() == "off":
        log.info("autoupdate: disabled (set ANIMICA_AUTOUPDATE=restart to enable)")
        return None
    if interval_h is None:
        interval_h = float(os.environ.get("ANIMICA_AUTOUPDATE_INTERVAL_H", "6") or 6)
    t = threading.Thread(target=_loop, args=(interval_h,), name="animica-autoupdate", daemon=True)
    t.start()
    log.warning("autoupdate: enabled (mode=%s, every ~%.1fh)", _mode(), interval_h)
    return t
