from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class HardwareProfile:
    logical_cores: int
    physical_cores: int
    ram_total_gib: float
    ram_available_gib: float
    disk_free_gib: float
    gpu_name: str | None = None
    gpu_vram_gib: float | None = None
    gpu_supports_bf16: bool = False
    gpu_supports_fp16: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HardwareProbe:
    @staticmethod
    def probe(output_dir: str | Path) -> HardwareProfile:
        logical = os.cpu_count() or 1
        physical = HardwareProbe._physical_cores(logical)
        ram_total, ram_available = HardwareProbe._ram_stats()
        free = shutil.disk_usage(Path(output_dir).expanduser()).free / (1024**3)
        gpu = HardwareProbe._gpu_stats()
        return HardwareProfile(
            logical_cores=logical,
            physical_cores=physical,
            ram_total_gib=round(ram_total, 2),
            ram_available_gib=round(ram_available, 2),
            disk_free_gib=round(free, 2),
            gpu_name=gpu.get("name"),
            gpu_vram_gib=gpu.get("vram_gib"),
            gpu_supports_bf16=bool(gpu.get("bf16", False)),
            gpu_supports_fp16=bool(gpu.get("fp16", False)),
        )

    @staticmethod
    def _physical_cores(default: int) -> int:
        try:
            import psutil

            return int(psutil.cpu_count(logical=False) or default)
        except Exception:
            return max(1, default // 2)

    @staticmethod
    def _ram_stats() -> tuple[float, float]:
        try:
            import psutil

            vm = psutil.virtual_memory()
            return vm.total / (1024**3), vm.available / (1024**3)
        except Exception:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            total = (pages * page_size) / (1024**3)
            return float(total), float(total * 0.5)

    @staticmethod
    def _gpu_stats() -> dict[str, Any]:
        try:
            import torch

            if not torch.cuda.is_available():
                return {}
            idx = 0
            props = torch.cuda.get_device_properties(idx)
            major = int(getattr(props, "major", 0))
            return {
                "name": torch.cuda.get_device_name(idx),
                "vram_gib": round(props.total_memory / (1024**3), 2),
                "fp16": True,
                "bf16": major >= 8,
            }
        except Exception:
            return {}
