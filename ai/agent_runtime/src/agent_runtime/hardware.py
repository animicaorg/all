"""Hardware detection.

Used by:
- Phase 7b miner auto-AICF registration (advertise capabilities to AICF)
- Phase 9 `animica miner pool connect` (advertise capabilities to pool)
- Phase 5 `animica chat` debug screen
- The local fallback bundle inference (decide precision/quant tier)

Detection is best-effort and pure-Python (no nvidia-smi shelling unless
available). When detection fails we record the failure and fall back to
the safest assumption (CPU-only, "tiny" tier).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class GPUInfo:
    index: int = 0
    name: str = ""
    vram_gb: float = 0.0
    driver: str = ""
    compute_capability: Optional[str] = None
    backend: str = ""    # cuda | rocm | mps | unknown


@dataclass
class HardwareProfile:
    os: str = ""
    arch: str = ""
    cpu_model: str = ""
    cpu_cores_physical: int = 0
    cpu_cores_logical: int = 0
    ram_gb: float = 0.0
    gpus: list[GPUInfo] = field(default_factory=list)
    accelerator_preferred: str = "cpu"   # cuda | mps | cpu
    # Eligible model tiers based on min_vram_gb / min_ram_gb thresholds from
    # ai/configs/model_catalog.yaml.
    eligible_tiers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Detection                                                                   #
# --------------------------------------------------------------------------- #

def _read_proc_cpuinfo() -> dict[str, str]:
    info: dict[str, str] = {}
    p = "/proc/cpuinfo"
    if not os.path.exists(p):
        return info
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip()
                if k and k not in info:
                    info[k] = v
    except OSError:
        pass
    return info


def _detect_cpu(profile: HardwareProfile) -> None:
    profile.cpu_cores_logical = os.cpu_count() or 0
    cpuinfo = _read_proc_cpuinfo()
    if cpuinfo:
        profile.cpu_model = cpuinfo.get("model name", "") or cpuinfo.get(
            "Model name", "")
        # Estimate physical cores by counting unique core id values.
        # Falls back to logical if /proc/cpuinfo doesn't expose them.
        try:
            ids = set()
            with open("/proc/cpuinfo", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("core id"):
                        ids.add(line.split(":")[1].strip())
            profile.cpu_cores_physical = len(ids) or profile.cpu_cores_logical
        except OSError:
            profile.cpu_cores_physical = profile.cpu_cores_logical
    else:
        # macOS / BSD path
        profile.cpu_model = platform.processor() or platform.machine()
        profile.cpu_cores_physical = profile.cpu_cores_logical


def _detect_ram(profile: HardwareProfile) -> None:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        profile.ram_gb = float(parts[1]) / (1024 * 1024)
                    break
    except OSError:
        pass
    if profile.ram_gb == 0.0:
        # macOS path: sysctl
        sysctl = shutil.which("sysctl")
        if sysctl:
            try:
                out = subprocess.check_output(
                    [sysctl, "-n", "hw.memsize"],
                    text=True, timeout=2,
                ).strip()
                profile.ram_gb = float(out) / (1024 ** 3)
            except (subprocess.SubprocessError, ValueError, OSError):
                pass


def _detect_nvidia_gpus(profile: HardwareProfile) -> None:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return
    try:
        out = subprocess.check_output(
            [smi, "--query-gpu=index,name,memory.total,driver_version,compute_cap",
             "--format=csv,noheader,nounits"],
            text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        profile.notes.append(f"nvidia-smi failed: {exc}")
        return
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            idx = int(parts[0])
            vram_mb = float(parts[2])
        except ValueError:
            continue
        gpu = GPUInfo(
            index=idx,
            name=parts[1],
            vram_gb=round(vram_mb / 1024.0, 2),
            driver=parts[3],
            compute_capability=parts[4] if len(parts) >= 5 else None,
            backend="cuda",
        )
        profile.gpus.append(gpu)


def _detect_rocm_gpus(profile: HardwareProfile) -> None:
    smi = shutil.which("rocm-smi")
    if not smi:
        return
    try:
        out = subprocess.check_output(
            [smi, "--showproductname", "--showmeminfo", "vram", "--json"],
            text=True, timeout=5,
        )
        data = json.loads(out)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        profile.notes.append(f"rocm-smi failed: {exc}")
        return
    for idx, (key, body) in enumerate(sorted(data.items())):
        if not isinstance(body, dict):
            continue
        vram_b = body.get("VRAM Total Memory (B)") or body.get(
            "VRAM Used Memory (B)") or 0
        try:
            vram_gb = float(vram_b) / (1024 ** 3)
        except (ValueError, TypeError):
            vram_gb = 0.0
        gpu = GPUInfo(
            index=idx,
            name=str(body.get("Card series", body.get("Card model", "AMD GPU"))),
            vram_gb=round(vram_gb, 2),
            driver="",
            compute_capability=None,
            backend="rocm",
        )
        profile.gpus.append(gpu)


def _detect_mps(profile: HardwareProfile) -> None:
    if platform.system() != "Darwin":
        return
    # Apple Silicon — exact VRAM is unified with RAM. Use a 50% slice as a
    # rough usable budget; the trainer will read real free memory at runtime.
    if platform.machine().lower().startswith("arm"):
        profile.gpus.append(GPUInfo(
            index=0,
            name="Apple Silicon (unified)",
            vram_gb=round(profile.ram_gb * 0.5, 2),
            driver="metal",
            backend="mps",
        ))


def _pick_accelerator(profile: HardwareProfile) -> str:
    for g in profile.gpus:
        if g.backend == "cuda" and g.vram_gb > 0:
            return "cuda"
    for g in profile.gpus:
        if g.backend == "rocm" and g.vram_gb > 0:
            return "cuda"   # treated as cuda for routing purposes
    for g in profile.gpus:
        if g.backend == "mps":
            return "mps"
    return "cpu"


def detect_hardware() -> HardwareProfile:
    """Synchronous best-effort hardware detection. Never raises."""
    profile = HardwareProfile(
        os=platform.system(),
        arch=platform.machine(),
    )
    try:
        _detect_cpu(profile)
        _detect_ram(profile)
        _detect_nvidia_gpus(profile)
        _detect_rocm_gpus(profile)
        _detect_mps(profile)
        profile.accelerator_preferred = _pick_accelerator(profile)
    except Exception as exc:   # noqa: BLE001 — best-effort detector
        profile.notes.append(f"detect_hardware partial failure: {exc}")
    return profile


# --------------------------------------------------------------------------- #
# Tier eligibility                                                            #
# --------------------------------------------------------------------------- #

def eligible_tiers(profile: HardwareProfile,
                   catalog: dict) -> list[str]:
    """Return tier ids the machine can plausibly serve, lowest to highest.

    A tier is eligible iff RAM >= min_ram_gb AND at least one GPU's
    vram_gb >= tier.min_vram_gb (or min_vram_gb is 0, meaning CPU-runnable).
    """
    out: list[str] = []
    tiers = catalog.get("tiers") or []
    max_vram = max((g.vram_gb for g in profile.gpus), default=0.0)
    for t in tiers:
        if not isinstance(t, dict):
            continue
        min_vram = float(t.get("min_vram_gb", 0))
        min_ram = float(t.get("min_ram_gb", 0))
        if profile.ram_gb + 0.5 < min_ram:
            continue
        if min_vram == 0:
            out.append(str(t["id"]))
        elif max_vram + 0.5 >= min_vram:
            out.append(str(t["id"]))
    return out


def attach_eligible_tiers(profile: HardwareProfile, catalog: dict) -> None:
    profile.eligible_tiers = eligible_tiers(profile, catalog)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def _main() -> int:
    profile = detect_hardware()
    # Best-effort: pull the catalog and annotate eligible tiers.
    try:
        from agent_runtime.config import load_config
        cfg = load_config()
        attach_eligible_tiers(profile, dict(cfg.model_catalog))
    except Exception:  # noqa: BLE001
        pass
    print(json.dumps(profile.to_dict(), indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
