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
    # Concrete accelerator family: "cuda" (NVIDIA), "rocm" (AMD), "mps" (Apple
    # Silicon / Metal), or "" when CPU-only. Drives which --device the miner gets.
    device_kind: str = ""

    @property
    def qualified_bittensor(self) -> bool:
        """Eligible to also serve Bittensor (GPU with enough VRAM)."""
        return self.gpu and self.vram_gb >= QUALIFIED_MIN_VRAM_GB

    def to_dict(self) -> dict:
        return {"cpu_count": self.cpu_count, "gpu": self.gpu,
                "gpu_name": self.gpu_name, "vram_gb": self.vram_gb,
                "cuda": self.cuda, "device_kind": self.device_kind,
                "qualified_bittensor": self.qualified_bittensor}


def _system_memory_gb() -> float:
    """Total system RAM in GiB. Apple Silicon uses *unified* memory, so this is
    the right proxy for usable 'VRAM' on Metal."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True, timeout=5)
            v = (out.stdout or "").strip()
            if v.isdigit():
                return round(int(v) / (1024 ** 3), 1)
    except Exception:
        pass
    try:  # POSIX (Linux + macOS)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
                     / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _apple_gpu_name() -> str:
    try:
        out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                             capture_output=True, text=True, timeout=5)
        chip = (out.stdout or "").strip()
        if chip:
            return f"{chip} (Metal)"
    except Exception:
        pass
    return "Apple GPU (Metal)"


def _detect_gpu_via_torch() -> Optional[tuple[str, float, str]]:
    """Detect a torch-visible accelerator across vendors.

    Returns ``(name, vram_gb, device_kind)`` with device_kind in
    {"cuda", "rocm", "mps"}, or None. torch is the primary path because the
    accelerator deps ship with the package, so this resolves NVIDIA, AMD
    (ROCm), and Apple Silicon (MPS) uniformly.
    """
    try:
        import torch  # type: ignore
    except Exception:
        return None
    # NVIDIA CUDA *and* AMD ROCm both surface through torch.cuda; ROCm wheels
    # set torch.version.hip. Covers the overwhelming majority of discrete GPUs.
    try:
        if torch.cuda.is_available() and torch.cuda.device_count() > 0:
            n = torch.cuda.device_count()
            name = torch.cuda.get_device_name(0)
            # Pool VRAM across ALL visible GPUs so a multi-GPU rig can serve a larger
            # tier (the inference engine shards via device_map="auto"). But discount a
            # per-GPU overhead on multi-GPU rigs: the naive sum overstates usable
            # memory and would let a rig advertise a tier it can only OOM/offload on.
            vram_sum = sum(
                torch.cuda.get_device_properties(i).total_memory for i in range(n)
            ) / (1024 ** 3)
            vram = vram_sum - n * _MULTI_GPU_OVERHEAD_GB if n > 1 else vram_sum
            vram = max(0.0, vram)
            hip = getattr(getattr(torch, "version", None), "hip", None)
            if n > 1:
                name = f"{n}x {name}"
            return name, round(float(vram), 1), ("rocm" if hip else "cuda")
    except Exception:
        pass
    # Apple Silicon / Metal Performance Shaders.
    try:
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return _apple_gpu_name(), _system_memory_gb(), "mps"
    except Exception:
        pass
    return None


def _detect_gpu_via_smi() -> Optional[tuple[str, float, str]]:
    """Vendor-CLI fallback for when torch is absent or a CPU-only build.

    Returns ``(name, vram_gb, device_kind)`` or None. Handles NVIDIA
    (nvidia-smi) and AMD (rocm-smi).
    """
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10)
            lines = (out.stdout or "").strip().splitlines()
            if out.returncode == 0 and lines:
                # Aggregate VRAM across every GPU nvidia-smi reports (one line per
                # card), not just the first — a multi-GPU rig pools its cards to
                # serve a larger, sharded model. Parse each line independently so one
                # malformed value (e.g. "[N/A]" on some MIG/vGPU configs) skips that
                # card instead of aborting the whole rig to CPU-only.
                total = 0.0
                valid = 0
                first_name = None
                for ln in lines:
                    parts = [p.strip() for p in ln.split(",")]
                    if not parts or not parts[0]:
                        continue
                    try:
                        if len(parts) > 1 and parts[1]:
                            total += float(parts[1]) / 1024.0
                            valid += 1
                            # name the rig after the first card we actually counted,
                            # not a skipped [N/A] one
                            if first_name is None:
                                first_name = parts[0]
                    except (ValueError, TypeError):
                        continue
                if valid == 0:
                    return None
                # Discount per-GPU overhead on multi-GPU rigs (see torch path).
                vram = total - valid * _MULTI_GPU_OVERHEAD_GB if valid > 1 else total
                vram = max(0.0, vram)
                name = (f"{valid}x {first_name}" if valid > 1 and first_name
                        else (first_name or "NVIDIA GPU"))
                return name, round(vram, 1), "cuda"
        except Exception:
            pass
    exe = shutil.which("rocm-smi")
    if exe:
        try:
            return _detect_rocm_smi(exe)
        except Exception:
            return ("AMD GPU (ROCm)", 0.0, "rocm")
    return None


def _detect_rocm_smi(exe: str) -> tuple[str, float, str]:
    """Best-effort AMD detection. rocm-smi output varies by version, so we read
    what we can and degrade to a generic name with 0 GB rather than failing."""
    name, vram = "AMD GPU (ROCm)", 0.0
    try:
        out = subprocess.run([exe, "--showproductname"],
                             capture_output=True, text=True, timeout=10)
        for ln in (out.stdout or "").splitlines():
            if ":" in ln and ("Card series" in ln or "Card model" in ln
                              or "Device Name" in ln):
                cand = ln.split(":", 1)[1].strip()
                if cand:
                    name = cand
                    break
    except Exception:
        pass
    try:
        out = subprocess.run([exe, "--showmeminfo", "vram"],
                             capture_output=True, text=True, timeout=10)
        for ln in (out.stdout or "").splitlines():
            low = ln.lower()
            if "total" in low and ":" in ln:
                digits = "".join(ch for ch in ln.split(":", 1)[1] if ch.isdigit())
                if digits:
                    vram = round(int(digits) / (1024 ** 3), 1)
                    break
    except Exception:
        pass
    return name, vram, "rocm"


def _amd_gpu_present() -> bool:
    """True if AMD/ROCm hardware looks present, regardless of whether the
    installed torch can use it (/dev/kfd, rocm-smi, or an AMD VGA in lspci)."""
    try:
        if os.path.exists("/dev/kfd"):
            return True
    except Exception:
        pass
    if shutil.which("rocm-smi"):
        return True
    try:
        out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5)
        for ln in (out.stdout or "").splitlines():
            low = ln.lower()
            if ("vga" in low or "display" in low or "3d" in low) and (
                "amd" in low or "radeon" in low or "advanced micro" in low
            ):
                return True
    except Exception:
        pass
    return False


def _torch_cuda_build_unusable() -> bool:
    """True if torch is installed but is a CUDA/CPU build with no usable GPU
    (torch.version.hip is None and torch.cuda is unavailable) — i.e. it cannot
    drive a ROCm/AMD GPU."""
    try:
        import torch  # type: ignore
    except Exception:
        return False
    try:
        hip = getattr(getattr(torch, "version", None), "hip", None)
        return hip is None and not torch.cuda.is_available()
    except Exception:
        return False


def _maybe_hint_rocm_torch() -> None:
    """If an AMD GPU is present but the installed torch can't use it (CUDA build),
    print an actionable hint — otherwise the GPU silently falls back to CPU."""
    try:
        if _torch_cuda_build_unusable() and _amd_gpu_present():
            print("[up] AMD GPU detected, but the installed PyTorch is a CUDA build "
                  "and cannot use ROCm — inference/mining will run on CPU.")
            print("[up] Install the ROCm PyTorch wheel (match your ROCm version), e.g.:")
            print("[up]   pip uninstall -y torch && pip install --index-url "
                  "https://download.pytorch.org/whl/rocm6.2 torch")
            print("[up] Then re-run. Consumer Radeons may also need "
                  "HSA_OVERRIDE_GFX_VERSION (e.g. 11.0.0 RDNA3, 10.3.0 RDNA2).")
    except Exception:
        pass


def detect_capabilities() -> Capabilities:
    """Detect CPU/GPU/VRAM across NVIDIA/AMD/Apple. Never raises; degrades to
    CPU-only."""
    cpu = os.cpu_count() or 1
    found = _detect_gpu_via_torch() or _detect_gpu_via_smi()
    # If we couldn't get a torch-usable GPU but AMD hardware is present, the most
    # common cause is the default CUDA torch wheel on an AMD box — hint loudly.
    if found is None or _torch_cuda_build_unusable():
        _maybe_hint_rocm_torch()
    if found:
        name, vram, kind = found
        return Capabilities(cpu_count=cpu, gpu=True, gpu_name=name,
                            vram_gb=vram, cuda=(kind == "cuda"),
                            device_kind=kind)
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


def _cli_device_flag(caps: Capabilities) -> Optional[str]:
    """Map the detected accelerator to the miner's ``--device`` taxonomy
    (cuda / rocm / metal), or None for CPU-only. ``mps`` is exposed to the CLI
    as ``metal``. Falls back to ``cuda`` for legacy Capabilities that set only
    ``cuda=True`` without a device_kind."""
    kind = caps.device_kind or ("cuda" if caps.cuda else "")
    return {"cuda": "cuda", "rocm": "rocm", "mps": "metal"}.get(kind)


# Rough accelerator-memory (GB) a machine needs to actually LOAD + serve each
# tier's default coder model. Sized to the real serving footprint (quantized
# weights — 4/8-bit MLX/GGUF — plus KV/activations and an OS reserve), NOT a
# strict fp16 load: fp16 weights alone are ~2x these numbers (Coder-7B ~15 GB,
# Coder-32B ~65 GB), which is why `elite` needs an 80 GB-class accelerator.
# Advertising a tier the box can't load just wastes a multi-GB download and then
# fails at serve time — e.g. an M2 Mac mini should not claim `elite` (Coder-32B)
# merely because Metal counts as a GPU.
_TIER_MIN_MEM_GB: tuple[tuple[str, float], ...] = (
    ("free",     3.0),    # ~0.5B
    ("standard", 7.0),    # ~3B  coder
    ("premium",  15.0),   # ~7B  coder
    ("elite",    72.0),   # ~32B coder — needs an 80 GB-class card (fp16 ~65 GB)
)

# Per-GPU usable-VRAM overhead (GB) subtracted from a MULTI-GPU rig's pooled total
# before tier-gating. A naive sum overstates what the rig can serve: each card holds
# its own CUDA context/reserve, fragmentation isn't shared across cards, and
# device_map sharding concentrates KV-cache/activations on whichever card runs a
# layer. Without this discount a rig of small cards (e.g. 3×24=72 GB) would cross the
# elite gate calibrated for one coherent 80 GB card, then OOM or silently CPU-offload
# the 32B model (serving at 10-100× slowdown). Single-GPU rigs are NOT discounted.
_MULTI_GPU_OVERHEAD_GB = 1.5


def _eligible_aicf_tiers(caps: Capabilities) -> list[str]:
    """Which AICF tiers this machine can actually serve, gated by memory.

    GPUs use VRAM (Apple Silicon uses unified/system memory); CPU-only boxes use
    system RAM and are capped at ``standard`` because larger models are
    impractically slow to decode on CPU. An explicit ``ANIMICA_AICF_TIERS``
    overrides this. Always returns at least ``["free"]`` so the miner still
    serves something.

    Scope: this governs the tiers the pool/stratum-facing worker advertises
    (``reference_cpu_miner`` reads ``ANIMICA_AICF_TIERS``). The separate
    ``agent_runtime`` AICF-broker worker gates its own tiers by hardware via its
    model catalog and does not read this env — its tier vocabulary differs
    (unifying the two is tracked separately).
    """
    override = os.environ.get("ANIMICA_AICF_TIERS", "").strip()
    if override:
        return [t.strip() for t in override.split(",") if t.strip()]
    mem_gb = float(caps.vram_gb or 0.0) if caps.gpu else _system_memory_gb()
    cpu_ceiling = None if caps.gpu else "standard"
    tiers: list[str] = []
    for tier, need in _TIER_MIN_MEM_GB:
        if mem_gb + 1e-6 >= need:
            tiers.append(tier)
        if cpu_ceiling and tier == cpu_ceiling:
            break
    return tiers or ["free"]


def _coordinator_endpoint(pool_host: str) -> str:
    """ENA coordinator base URL for a pool host: ``pool.animica.org`` →
    ``https://pool.animica.org/api/ena`` (the nginx route fronting the
    coordinator). Pass-through if it already looks like a URL / targets /api/ena.
    """
    host = (pool_host or "").strip().rstrip("/")
    if not host:
        return ""
    if "://" not in host:
        host = "https://" + host
    if "/api/ena" in host or host.endswith("/ena"):
        return host
    return host + "/api/ena"


def _node_rpc_url(cfg: "UnifiedConfig") -> str:
    """JSON-RPC URL for the chain node the AICF broker lives behind.

    The Studio worker claims ``function_compute`` jobs from the broker over
    JSON-RPC (``ANIMICA_RPC_URL``). Without this, a miner running ``animica up``
    with no local node fell back to ``http://127.0.0.1:8545`` and every claim got
    ECONNREFUSED (errno 61). With ``--with-node`` we run a local node; otherwise
    point at the pool's public RPC (``rpc.<pool-domain>``, e.g. pool.animica.org →
    https://rpc.animica.org), which fronts the same broker.
    """
    if cfg.run_node:
        return "http://127.0.0.1:8545"
    host = (cfg.pool_host or "").strip().rstrip("/")
    if "://" in host:                       # already a URL → derive nothing
        return "https://rpc.animica.org"
    if host.startswith("pool."):
        return "https://rpc." + host[len("pool."):]
    return "https://rpc.animica.org"


def _resolve_best_pool(pool_host: str, *, timeout: float = 6.0) -> Optional[str]:
    """Best-effort: pick the highest-paying open training pool from the pool's
    public coordinator API. Returns a pool_id or None (never raises).

    "Highest paying" = the largest confirmed, not-yet-paid funding pot
    (``Pool.budget_nano``) among pools still accepting trainers (status open or
    training). A 0-budget pool is chosen only when nothing funded is available,
    so ``animica up`` still contributes to the one global model.
    """
    import json
    import urllib.request

    base = _coordinator_endpoint(pool_host)
    if not base:
        return None
    url = base + "/pool/list"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "animica-up"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    pools = payload.get("pools") if isinstance(payload, dict) else None
    if not isinstance(pools, list):
        return None
    active = {"open", "training"}
    candidates: list[tuple[int, str]] = []
    for p in pools:
        if not isinstance(p, dict):
            continue
        if str(p.get("status") or "").lower() not in active:
            continue
        pid = p.get("pool_id")
        if not pid:
            continue
        try:
            budget = int(p.get("budget_nano") or 0)
        except (TypeError, ValueError):
            budget = 0
        candidates.append((budget, str(pid)))
    if not candidates:
        return None
    # Highest budget first; deterministic tie-break by pool_id.
    candidates.sort(key=lambda t: (-t[0], t[1]))
    return candidates[0][1]


def build_plan(caps: Capabilities, cfg: UnifiedConfig) -> list[Component]:
    """Deterministic component plan for these capabilities + config.

    Pure: launches nothing. Every component is bound to ``cfg.address`` so all
    rewards (PoW, useful-work, training, serving, Bittensor) settle to one ANM
    address.
    """
    a = animica_cmd()
    wid = cfg.wid()
    gpu, qual = caps.gpu, caps.qualified_bittensor
    # ANIMICA_RPC_URL: the chain RPC the AICF broker lives behind. The Studio
    # worker claims function_compute jobs over JSON-RPC against it — point it at
    # the local node (--with-node) or the pool's public RPC so a node-less miner
    # doesn't get ECONNREFUSED on every claim.
    base_env = {"ANIMICA_MINER_VERSION": UNIFIED_VERSION,
                "ANIMICA_PAYOUT_ADDRESS": cfg.address,
                "ANIMICA_RPC_URL": _node_rpc_url(cfg)}
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
    # Pass the *concrete* accelerator so the miner targets the right torch
    # backend (cuda / rocm / metal). The old `--gpu` hard-coded CUDA, which
    # left Apple Silicon (Metal/MPS) on the CPU. The miner falls back to CPU on
    # its own if torch can't actually use the device.
    device_flag = _cli_device_flag(caps)
    if device_flag:
        miner_argv += ["--device", device_flag]
    if cfg.threads:
        miner_argv += ["--threads", str(cfg.threads)]
    miner_env = dict(base_env)
    # Gate the pool/stratum worker's serving tiers by the memory this machine
    # actually has, so it only advertises models it can load (e.g. an M2 mini
    # serves standard/premium but not elite/32B). ANIMICA_AICF_TIERS overrides.
    aicf_tiers = _eligible_aicf_tiers(caps)
    miner_env["ANIMICA_AICF_TIERS"] = ",".join(aicf_tiers)
    plan.append(Component("miner", miner_argv, enabled=True,
                          reason=f"SHA3 proof-of-work + AICF inference (tiers: {','.join(aicf_tiers)})",
                          env=miner_env))

    # The ENA coordinator (pools, shards, useful-work queue) lives on the pool
    # host, not this box. Point every ENA component at it over HTTP so workers
    # claim/download/submit against the real global state instead of an empty
    # local store (which is what produced "pool not found").
    coord = _coordinator_endpoint(cfg.pool_host)

    # ENA useful-work (CPU jobs) — always
    plan.append(Component(
        "useful-work",
        a + ["ena", "worker", "start", "--worker-id", wid]
          + (["--endpoint", coord] if coord else []),
        enabled=True, reason="CPU useful-work (scrape/clean/embed/eval)",
        env=dict(base_env)))

    # Animica Studio — serve serverless function_compute jobs. Every rig (CPU or
    # GPU) can run Studio functions, so the same hardware that mines and trains
    # also executes users' (and agents') code, all paid to cfg.address. GPU rigs
    # advertise the GPU capability so gpu-pinned functions route here.
    studio_argv = a + ["studio", "worker", "--address", cfg.address,
                       "--tiers", "function_compute"]
    if gpu:
        studio_argv += ["--gpu"]
    plan.append(Component(
        "studio", studio_argv, enabled=True,
        reason="serves Animica Studio serverless functions (GPU-tier)" if gpu
               else "serves Animica Studio serverless functions",
        env=dict(base_env)))

    # GPU: train pool shards toward the global model
    plan.append(Component(
        "trainer",
        a + ["ena", "pool", "train-loop", (cfg.pool_id or "<pool>"),
             "--worker-id", wid, "--address", cfg.address]
          + (["--endpoint", coord] if coord else []),
        enabled=gpu and bool(cfg.pool_id),
        reason=("trains pool shards" if gpu and cfg.pool_id else
                ("GPU present but no training pool available "
                 "(could not auto-select one)" if gpu else "no GPU")),
        env=dict(base_env)))

    # GPU: serve the promoted checkpoint (OpenAI-compatible), ANM-credited
    plan.append(Component(
        "server",
        a + ["ena", "pool", "serve", (cfg.pool_id or "<pool>"),
             "--worker-id", wid, "--address", cfg.address,
             "--port", str(cfg.serve_port)]
          + (["--endpoint", coord] if coord else []),
        enabled=gpu and bool(cfg.pool_id),
        reason=("serves the promoted checkpoint" if gpu and cfg.pool_id else
                ("GPU present but no training pool available "
                 "(could not auto-select one)" if gpu else "no GPU")),
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
