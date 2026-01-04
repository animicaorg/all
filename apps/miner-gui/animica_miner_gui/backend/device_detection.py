"""Device detection and autodetection utilities for mining hardware.

Provides robust detection of CPU, GPU (OpenCL), and ASIC (stub) devices
with safe defaults and defensive error handling.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CPUInfo:
    """Detected CPU information."""
    cores: int
    threads: int
    model_name: str
    vendor: str
    hugepages_available: bool
    recommended_threads: int
    in_container: bool
    container_cpu_limit: Optional[float]


@dataclass
class GPUInfo:
    """Detected GPU device information."""
    device_id: int
    name: str
    vendor: str
    compute_units: int
    memory_mb: int
    driver_version: str
    opencl_version: str
    recommended: bool


@dataclass
class DetectionResult:
    """Complete device detection result."""
    cpu: CPUInfo
    gpus: List[GPUInfo]
    has_opencl: bool
    warnings: List[str]
    recommendations: List[str]


def detect_cpu() -> CPUInfo:
    """Detect CPU capabilities and configuration.
    
    Returns:
        CPUInfo with detected CPU details and recommendations.
    """
    import multiprocessing
    
    cores = os.cpu_count() or 1
    threads = multiprocessing.cpu_count()
    
    # Detect model name
    model_name = "Unknown CPU"
    vendor = "Unknown"
    
    system = platform.system()
    
    if system == "Linux":
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        model_name = line.split(":", 1)[1].strip()
                        break
                    if "vendor_id" in line:
                        vendor = line.split(":", 1)[1].strip()
        except Exception as e:
            logger.debug(f"Could not read /proc/cpuinfo: {e}")
    elif system == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                model_name = result.stdout.strip()
                vendor = "Apple" if "Apple" in model_name else "Intel"
        except Exception as e:
            logger.debug(f"Could not detect macOS CPU: {e}")
    elif system == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            model_name = winreg.QueryValueEx(key, "ProcessorNameString")[0]
            vendor = winreg.QueryValueEx(key, "VendorIdentifier")[0]
            winreg.CloseKey(key)
        except Exception as e:
            logger.debug(f"Could not detect Windows CPU: {e}")
    
    # Check for hugepages (Linux only)
    hugepages_available = False
    if system == "Linux":
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if "HugePages_Total" in line:
                        total = int(line.split(":")[1].strip())
                        hugepages_available = total > 0
                        break
        except Exception:
            pass
    
    # Check if running in container
    in_container = False
    container_cpu_limit = None
    
    if system == "Linux":
        # Check for container indicators
        if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
            in_container = True
        
        # Check cgroup CPU limits
        try:
            with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r") as f:
                quota = int(f.read().strip())
            with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r") as f:
                period = int(f.read().strip())
            if quota > 0:
                container_cpu_limit = quota / period
        except Exception:
            # Try cgroups v2
            try:
                with open("/sys/fs/cgroup/cpu.max", "r") as f:
                    parts = f.read().strip().split()
                    if parts[0] != "max":
                        quota = int(parts[0])
                        period = int(parts[1])
                        container_cpu_limit = quota / period
            except Exception:
                pass
    
    # Recommend threads (leave 1-2 cores for system)
    if container_cpu_limit:
        recommended_threads = max(1, int(container_cpu_limit) - 1)
    else:
        recommended_threads = max(1, threads - 1)
    
    return CPUInfo(
        cores=cores,
        threads=threads,
        model_name=model_name,
        vendor=vendor,
        hugepages_available=hugepages_available,
        recommended_threads=recommended_threads,
        in_container=in_container,
        container_cpu_limit=container_cpu_limit
    )


def detect_gpus() -> Tuple[List[GPUInfo], bool]:
    """Detect available GPU devices via OpenCL.
    
    Returns:
        Tuple of (list of GPUInfo, has_opencl_support)
    """
    gpus: List[GPUInfo] = []
    has_opencl = False
    
    try:
        import pyopencl as cl
        has_opencl = True
        
        platforms = cl.get_platforms()
        device_id = 0
        
        for platform in platforms:
            devices = platform.get_devices(device_type=cl.device_type.GPU)
            
            for device in devices:
                try:
                    name = device.name.strip()
                    vendor = device.vendor.strip()
                    compute_units = device.max_compute_units
                    memory_mb = device.global_mem_size // (1024 * 1024)
                    driver_version = device.driver_version.strip()
                    opencl_version = device.opencl_c_version.strip()
                    
                    # Recommend NVIDIA and AMD GPUs with >2GB memory
                    recommended = (
                        ("NVIDIA" in vendor or "AMD" in vendor or "Intel" in vendor)
                        and memory_mb >= 2048
                        and compute_units >= 4
                    )
                    
                    gpus.append(GPUInfo(
                        device_id=device_id,
                        name=name,
                        vendor=vendor,
                        compute_units=compute_units,
                        memory_mb=memory_mb,
                        driver_version=driver_version,
                        opencl_version=opencl_version,
                        recommended=recommended
                    ))
                    device_id += 1
                    
                except Exception as e:
                    logger.debug(f"Error querying GPU device: {e}")
                    continue
    
    except ImportError:
        logger.debug("pyopencl not available - GPU detection disabled")
    except Exception as e:
        logger.warning(f"Error detecting GPUs: {e}")
    
    return gpus, has_opencl


def detect_all() -> DetectionResult:
    """Run complete device detection with recommendations.
    
    Returns:
        DetectionResult with all detected hardware and recommendations.
    """
    cpu = detect_cpu()
    gpus, has_opencl = detect_gpus()
    
    warnings: List[str] = []
    recommendations: List[str] = []
    
    # CPU warnings and recommendations
    if cpu.in_container and cpu.container_cpu_limit:
        warnings.append(
            f"Running in container with CPU limit: {cpu.container_cpu_limit:.2f} cores"
        )
        recommendations.append(
            f"Recommended threads: {cpu.recommended_threads} (container-aware)"
        )
    elif cpu.threads > 8:
        recommendations.append(
            f"Recommended threads: {cpu.recommended_threads} (leaving cores for system)"
        )
    
    if not cpu.hugepages_available and platform.system() == "Linux":
        recommendations.append(
            "Consider enabling hugepages for better performance: "
            "sudo sysctl -w vm.nr_hugepages=128"
        )
    
    # GPU warnings and recommendations
    if not has_opencl:
        warnings.append(
            "OpenCL not available - GPU mining disabled. "
            "Install pyopencl for GPU support: pip install pyopencl"
        )
    elif not gpus:
        warnings.append("No OpenCL GPU devices detected")
    else:
        recommended_gpus = [g for g in gpus if g.recommended]
        if recommended_gpus:
            gpu_names = ", ".join(g.name for g in recommended_gpus)
            recommendations.append(f"Recommended GPUs: {gpu_names}")
        else:
            warnings.append(
                "Detected GPUs may not be suitable for mining "
                "(low compute units or memory)"
            )
    
    # RAM-aware recommendations
    try:
        if platform.system() == "Linux":
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if "MemAvailable" in line:
                        mem_kb = int(line.split(":")[1].strip().split()[0])
                        mem_gb = mem_kb / (1024 * 1024)
                        if mem_gb < 4:
                            warnings.append(
                                f"Low available memory: {mem_gb:.1f} GB. "
                                "Consider enabling Safe Mode."
                            )
                        break
    except Exception:
        pass
    
    return DetectionResult(
        cpu=cpu,
        gpus=gpus,
        has_opencl=has_opencl,
        warnings=warnings,
        recommendations=recommendations
    )


def get_safe_mode_config(detection: DetectionResult) -> dict:
    """Generate safe mode configuration based on detection.
    
    Args:
        detection: Device detection result
    
    Returns:
        Dictionary with safe mode settings
    """
    safe_config = {
        "cpu_threads": max(1, detection.cpu.recommended_threads - 1),
        "cpu_intensity": 0.7,
        "gpu_intensity": 0.5,
        "enable_safe_mode": False
    }
    
    # Enable safe mode if in container with limits or low memory
    if detection.cpu.in_container and detection.cpu.container_cpu_limit:
        if detection.cpu.container_cpu_limit < 2.0:
            safe_config["enable_safe_mode"] = True
    
    for warning in detection.warnings:
        if "Low available memory" in warning:
            safe_config["enable_safe_mode"] = True
            break
    
    return safe_config
