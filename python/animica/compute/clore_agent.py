"""Clore host-agent enrollment for opt-in third-party GPU compute.

Only ever reached when the machine operator has explicitly consented (see
cli/compute_consent.py). Installs Clore's host agent and links the machine to the
pool's Clore account so rental revenue accrues centrally and is shared back 90/10
as ANM.

Clore onboarding (real mechanism, operator-supplied 2026-08-17):

    bash <(curl -s https://gitlab.com/cloreai-public/hosting-agent-installer/-/raw/main/install.sh) \\
         --onboarding-config <base64>

The base64 blob is an ACCOUNT-level onboarding config, not a per-server token:

    {"auth": "<account key>", "mrl": 300,
     "autoprice": {"usd": true, "on_demand": 5, "spot": 5}}

so the SAME config enrolls every consenting miner into the pool's one account —
no per-machine token to mint. The pool serves this one blob to any consenting
worker via /api/compute/clore-token; the client passes it straight to the
installer.

Nothing here touches mining or serving; any failure logs and returns without
disturbing the rest of `animica up`.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Optional

_INSTALL_URL = ("https://gitlab.com/cloreai-public/hosting-agent-installer/-/raw/"
                "main/install.sh")
_STATE = Path(os.path.expanduser("~/.animica/clore-enroll.json"))
# The agent writes its config/marker here once onboarded.
_MARKERS = ("/opt/clore-hosting/client/auth", "/opt/clore-hosting/onboarding_config",
            "/etc/clore-hosting/config")


def is_enrolled() -> bool:
    for m in _MARKERS:
        try:
            if Path(m).exists() and Path(m).stat().st_size > 0:
                return True
        except Exception:
            pass
    try:
        return json.loads(_STATE.read_text()).get("enrolled") is True
    except Exception:
        return False


def _save_state(d: dict) -> None:
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    _STATE.write_text(json.dumps(d, indent=1, sort_keys=True))


def detect_gpu() -> str:
    """Best-effort NVIDIA GPU model, e.g. 'NVIDIA GeForce RTX 4090'."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name",
                              "--format=csv,noheader"], capture_output=True,
                             text=True, timeout=10)
        first = (out.stdout or "").strip().splitlines()
        return first[0].strip() if first else ""
    except Exception:
        return ""


def fetch_pool_token(pool_host: str, worker_id: str, address: str,
                     gpu: str = "") -> Optional[str]:
    """Fetch the pool's Clore onboarding config (base64), priced for this GPU.

    The value is the account-level --onboarding-config blob with autoprice set by
    the pool from live market rates for `gpu`. Returns None if the pool serves
    none (enrollment then skipped, fail-safe).
    """
    import urllib.request
    import urllib.parse
    q = urllib.parse.urlencode({"worker": worker_id or "", "address": address or "",
                                "gpu": gpu or detect_gpu()})
    url = f"https://{pool_host}/api/compute/clore-token?{q}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "animica-up/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode())
        tok = str(d.get("token") or "").strip()
        return tok or None
    except Exception:
        return None


def _valid_config(cfg_b64: str) -> bool:
    """Sanity-check the blob decodes to a Clore onboarding config with an auth key."""
    try:
        obj = json.loads(base64.b64decode(cfg_b64))
        return isinstance(obj, dict) and bool(obj.get("auth"))
    except Exception:
        return False


def enroll(console, *, token: str, assume_yes: bool = False, dry_run: bool = False) -> bool:
    """Install + onboard the Clore host agent with the account config. True on success."""
    cfg = (token or "").strip()
    if not cfg:
        console.print("[yellow]compute: no Clore onboarding config available — skipping[/yellow]")
        return False
    if not _valid_config(cfg):
        console.print("[red]compute: Clore onboarding config is malformed — skipping[/red]")
        return False
    if is_enrolled():
        console.print("[dim]compute: Clore agent already enrolled[/dim]")
        return True
    if os.geteuid() != 0:
        console.print("[yellow]compute: Clore install needs root; re-run `animica up` with "
                      "sudo, or `sudo animica compute enroll`[/yellow]")
        return False

    cmd = f"bash <(curl -s {_INSTALL_URL}) --onboarding-config {cfg}"
    console.print("[bold]compute: enrolling this GPU on Clore[/bold]")
    console.print(f"  [dim]bash <(curl -s {_INSTALL_URL}) --onboarding-config <config>[/dim]")
    if dry_run:
        console.print("  [dim](dry run — nothing executed)[/dim]")
        return False

    try:
        subprocess.run(["bash", "-c", cmd], check=True, timeout=1800)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]compute: Clore enrollment failed ({exc}); mining/serving unaffected[/red]")
        return False
    except Exception as exc:  # pragma: no cover - defensive
        console.print(f"[red]compute: Clore enrollment error ({exc}); mining/serving unaffected[/red]")
        return False

    ok = is_enrolled()
    _save_state({"enrolled": ok})
    if ok:
        console.print("[green]compute: enrolled — this GPU is now on Clore. A reboot may be "
                      "needed for Clore to mark it active.[/green]")
    else:
        console.print("[yellow]compute: installer ran but no enrollment marker found — "
                      "check `journalctl -u clore-hosting` or reboot[/yellow]")
    return ok


def unenroll(console) -> None:
    """Best-effort removal of the Clore agent (for `animica compute off`)."""
    import shutil
    try:
        for svc in ("clore-hosting", "clore"):
            subprocess.run(["systemctl", "stop", svc], timeout=30,
                           capture_output=True)
            subprocess.run(["systemctl", "disable", svc], timeout=30,
                           capture_output=True)
        if os.geteuid() == 0:
            for d in ("/opt/clore-hosting", "/etc/clore-hosting"):
                shutil.rmtree(d, ignore_errors=True)
        try:
            _STATE.unlink()
        except Exception:
            pass
        console.print("[green]compute: Clore agent stopped and removed[/green]")
    except Exception as exc:
        console.print(f"[yellow]compute: could not fully remove Clore agent ({exc}); "
                      f"stop it manually (systemctl stop clore-hosting)[/yellow]")
