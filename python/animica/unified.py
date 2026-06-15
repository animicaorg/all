"""
animica.unified
===============

One command to run everything. ``animica up`` detects the box's capabilities and
launches the right set of long-running components — all bound to a single ANM
payout address and the one pool / one global model:

* **miner**        — SHA3 proof-of-work (always)
* **useful-work**  — ENA CPU jobs: scrape/clean/embed/eval (always)
* **trainer**      — claims + trains pool shards            (GPU)
* **server**       — serves the promoted checkpoint          (GPU, OpenAI API)
* **bittensor**    — Bittensor serving, ANM-bound            (qualified GPU)
* **node**         — a local full node                       (opt-in)

Capability tiers: CPU-only → mine + useful-work; GPU → also train + serve;
"qualified" (GPU with >= 16 GB VRAM) → also Bittensor. Everything settles to the
miner's Animica address; there is no external (TAO/XMR) payout path here.

This module is import-light (stdlib only); GPU detection imports torch lazily and
falls back to ``nvidia-smi``. ``build_plan`` is pure and deterministic so the
launch plan can be inspected (``animica up --plan``) and unit-tested without
launching anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# Must match animica.stratum_pool.version_gate.UNIFIED_MINER_VERSION — the pool
# rejects miners advertising an older version.
UNIFIED_VERSION = "1.0.0"

QUALIFIED_MIN_VRAM_GB = 16.0


# ---------------------------------------------------------------------------
# capability detection
# ---------------------------------------------------------------------------

@dataclass
class Capabilities:
    cpu_count: int
    gpu: bool = False
    gpu_name: str = ""
    vram_gb: float = 0.0
    cuda: bool = False

    @property
    def qualified_bittensor(self) -> bool:
        """Eligible to also serve Bittensor (GPU with enough VRAM)."""
        return self.gpu and self.vram_gb >= QUALIFIED_MIN_VRAM_GB

    def to_dict(self) -> dict:
        return {"cpu_count": self.cpu_count, "gpu": self.gpu,
                "gpu_name": self.gpu_name, "vram_gb": self.vram_gb,
                "cuda": self.cuda, "qualified_bittensor": self.qualified_bittensor}


def _detect_gpu_via_torch() -> Optional[tuple[str, float, bool]]:
    try:  # pragma: no cover - exercised only where torch+CUDA exist
        import torch  # type: ignore
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
            return name, round(float(vram), 1), True
    except Exception:
        return None
    return None


def _detect_gpu_via_smi() -> Optional[tuple[str, float, bool]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10)
        line = (out.stdout or "").strip().splitlines()[:1]
        if out.returncode == 0 and line:
            parts = [p.strip() for p in line[0].split(",")]
            name = parts[0]
            vram = float(parts[1]) / 1024.0 if len(parts) > 1 else 0.0
            return name, round(vram, 1), False
    except Exception:
        return None
    return None


def detect_capabilities() -> Capabilities:
    """Detect CPU/GPU/VRAM. Never raises; degrades to CPU-only."""
    cpu = os.cpu_count() or 1
    found = _detect_gpu_via_torch() or _detect_gpu_via_smi()
    if found:
        name, vram, cuda = found
        return Capabilities(cpu_count=cpu, gpu=True, gpu_name=name,
                            vram_gb=vram, cuda=cuda)
    return Capabilities(cpu_count=cpu)


# ---------------------------------------------------------------------------
# launch plan
# ---------------------------------------------------------------------------

@dataclass
class UnifiedConfig:
    address: str                       # ANM payout address — everything binds here
    pool_host: str = "pool.animica.org"
    pool_port: int = 3333
    worker_id: str = ""
    pool_id: Optional[str] = None      # training pool / global model to train+serve
    serve_port: int = 8799
    run_node: bool = False
    threads: int = 0                   # 0 → miner default
    bittensor_token: Optional[str] = None  # SN51 enrollment token (or env)

    def wid(self) -> str:
        return self.worker_id or self.address


@dataclass
class Component:
    name: str
    argv: list[str]
    enabled: bool
    reason: str
    env: dict = field(default_factory=dict)
    available: bool = True             # False → intended but not yet runnable

    def to_dict(self) -> dict:
        return {"name": self.name, "enabled": self.enabled, "available": self.available,
                "reason": self.reason, "argv": self.argv, "env": self.env}


def _wallets_path():
    from animica.contracts.wallet_utils import wallet_store_path
    return wallet_store_path()


def _read_default_address() -> Optional[str]:
    """Best-effort: the default (or first) address in ~/.animica/wallets.json."""
    import json
    try:
        raw = json.loads(_wallets_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict) and e.get("address"):
                return str(e["address"])
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("default_address"):
        return str(raw["default_address"])
    wallets = raw.get("wallets")
    entries = wallets if isinstance(wallets, list) else [
        v for v in raw.values() if isinstance(v, dict)]
    default_label = raw.get("default")
    if default_label:
        for e in entries:
            if str(e.get("label") or "").strip() == str(default_label).strip():
                if e.get("address"):
                    return str(e["address"])
    for e in entries:
        if isinstance(e, dict) and e.get("address"):
            return str(e["address"])
    return None


def _create_wallet() -> Optional[str]:
    """Auto-create a wallet (so `animica up` is zero-config). Returns its address."""
    try:
        subprocess.run(
            animica_cmd() + ["wallet", "create", "--label", "animica-up",
                             "--allow-insecure-fallback"],
            capture_output=True, text=True, timeout=180)
    except Exception:
        return None
    return _read_default_address()


def resolve_address(explicit: Optional[str] = None, *,
                    create: bool = True) -> tuple[str, str]:
    """Resolve the ANM payout address for the one-command run.

    Order: explicit flag → ANIMICA_PAYOUT_ADDRESS/ANIMICA_MINER_ADDRESS env →
    default wallet in ~/.animica/wallets.json → (if ``create``) auto-create one.
    Returns ``(address, source)``; address is "" with source "none" if nothing
    is found and ``create`` is False.
    """
    if explicit and explicit.strip():
        return explicit.strip(), "flag"
    for ev in ("ANIMICA_PAYOUT_ADDRESS", "ANIMICA_MINER_ADDRESS"):
        v = os.environ.get(ev)
        if v and v.strip():
            return v.strip(), f"env:{ev}"
    addr = _read_default_address()
    if addr:
        return addr, "wallet"
    if create:
        created = _create_wallet()
        if created:
            return created, "created"
        raise RuntimeError(
            "no wallet found and auto-create failed — run `animica wallet create` "
            "then `animica up` (or pass --address)")
    return "", "none"


def animica_cmd() -> list[str]:
    """The base argv to invoke the animica CLI (prefer the installed script)."""
    exe = shutil.which("animica")
    if exe:
        return [exe]
    return [sys.executable, "-m", "animica.cli.main"]


def build_plan(caps: Capabilities, cfg: UnifiedConfig) -> list[Component]:
    """Deterministic component plan for these capabilities + config.

    Pure: launches nothing. Every component is bound to ``cfg.address`` so all
    rewards (PoW, useful-work, training, serving, Bittensor) settle to one ANM
    address.
    """
    a = animica_cmd()
    wid = cfg.wid()
    gpu, qual = caps.gpu, caps.qualified_bittensor
    base_env = {"ANIMICA_MINER_VERSION": UNIFIED_VERSION,
                "ANIMICA_PAYOUT_ADDRESS": cfg.address}
    plan: list[Component] = []

    # optional local node
    plan.append(Component(
        "node", a + ["node", "up", "--no-detach"],
        enabled=cfg.run_node, env=dict(base_env),
        reason="--with-node" if cfg.run_node else "off (point at the pool's RPC)"))

    # SHA3 proof-of-work + AICF inference — always. `miner start --aicf` runs PoW
    # and serves AICF inference jobs together; advertises UNIFIED_VERSION + tiers.
    miner_argv = a + ["miner", "start", "--pool", f"{cfg.pool_host}:{cfg.pool_port}",
                      "--address", cfg.address, "--aicf"]
    if gpu:
        miner_argv += ["--gpu"]
    if cfg.threads:
        miner_argv += ["--threads", str(cfg.threads)]
    miner_env = dict(base_env)
    miner_env["ANIMICA_AICF_TIERS"] = "standard,premium,elite" if gpu else "free,standard"
    plan.append(Component("miner", miner_argv, enabled=True,
                          reason="SHA3 proof-of-work + AICF inference", env=miner_env))

    # ENA useful-work (CPU jobs) — always
    plan.append(Component(
        "useful-work", a + ["ena", "worker", "start", "--worker-id", wid],
        enabled=True, reason="CPU useful-work (scrape/clean/embed/eval)",
        env=dict(base_env)))

    # GPU: train pool shards toward the global model
    plan.append(Component(
        "trainer",
        a + ["ena", "pool", "train-loop", (cfg.pool_id or "<pool>"),
             "--worker-id", wid, "--address", cfg.address],
        enabled=gpu and bool(cfg.pool_id),
        reason=("trains pool shards" if gpu and cfg.pool_id else
                ("GPU present but no --pool-id" if gpu else "no GPU")),
        env=dict(base_env)))

    # GPU: serve the promoted checkpoint (OpenAI-compatible), ANM-credited
    plan.append(Component(
        "server",
        a + ["ena", "pool", "serve", (cfg.pool_id or "<pool>"),
             "--worker-id", wid, "--address", cfg.address,
             "--port", str(cfg.serve_port)],
        enabled=gpu and bool(cfg.pool_id),
        reason=("serves the promoted checkpoint" if gpu and cfg.pool_id else
                ("GPU present but no --pool-id" if gpu else "no GPU")),
        env=dict(base_env)))

    # Qualified GPU: Bittensor serving via the shipped `animica bittensor` flow,
    # bound to ANM (the pool pays Bittensor earnings out in ANM to this address).
    # Needs an SN51 enrollment token from pool.animica.org/workers (the deploy
    # bridge); with it the component runs the real installer.
    bt_token = cfg.bittensor_token or os.environ.get("ANIMICA_WORKER_TOKEN", "")
    bt_env = dict(base_env)
    if bt_token:
        bt_env["ANIMICA_WORKER_TOKEN"] = bt_token
    if qual and bt_token:
        bt_reason = "qualified (GPU >= %.0f GB) — ANM-bound Bittensor SN51 serving" % QUALIFIED_MIN_VRAM_GB
    elif qual:
        bt_reason = ("qualified — set ANIMICA_WORKER_TOKEN (from "
                     "pool.animica.org/workers) to enroll, ANM-bound")
    elif gpu:
        bt_reason = "GPU under %.0f GB VRAM" % QUALIFIED_MIN_VRAM_GB
    else:
        bt_reason = "no GPU"
    plan.append(Component(
        "bittensor", a + ["bittensor", "up", "--run"],
        enabled=qual, reason=bt_reason, env=bt_env,
        available=bool(qual and bt_token)))

    return plan


def plan_summary(caps: Capabilities, cfg: UnifiedConfig,
                 plan: list[Component]) -> dict:
    active = [c.name for c in plan if c.enabled and c.available]
    planned = [c.name for c in plan if c.enabled and not c.available]
    return {
        "version": UNIFIED_VERSION,
        "address": cfg.address,
        "pool_host": cfg.pool_host,
        "pool_id": cfg.pool_id,
        "capabilities": caps.to_dict(),
        "will_run": active,
        "enabled_but_pending": planned,
        "components": [c.to_dict() for c in plan],
    }


# ---------------------------------------------------------------------------
# supervisor
# ---------------------------------------------------------------------------

class Supervisor:
    """Launches the enabled+available components and restarts them on exit."""

    def __init__(self, plan: list[Component], *, restart_backoff: float = 3.0) -> None:
        self.plan = [c for c in plan if c.enabled and c.available]
        self.restart_backoff = restart_backoff
        self._procs: dict[str, subprocess.Popen] = {}
        self._stop = False

    def _spawn(self, c: Component) -> None:
        env = {**os.environ, **c.env}
        self._procs[c.name] = subprocess.Popen(c.argv, env=env)

    def run(self) -> None:
        if not self.plan:
            print("[up] nothing to run for this capability tier")
            return
        for c in self.plan:
            print(f"[up] starting {c.name}: {' '.join(c.argv)}")
            self._spawn(c)
        try:
            while not self._stop:
                time.sleep(2.0)
                for c in self.plan:
                    p = self._procs.get(c.name)
                    if p is not None and p.poll() is not None:
                        print(f"[up] {c.name} exited ({p.returncode}); "
                              f"restarting in {self.restart_backoff}s")
                        time.sleep(self.restart_backoff)
                        self._spawn(c)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop = True
        for name, p in self._procs.items():
            if p.poll() is None:
                p.terminate()
        for p in self._procs.values():
            try:
                p.wait(timeout=10)
            except Exception:  # noqa: BLE001
                p.kill()
