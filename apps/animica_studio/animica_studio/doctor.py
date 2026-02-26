"""animica-studio doctor: structured environment health check.

Run via:
    animica-studio doctor          # prints human-readable report
    animica-studio doctor --json   # prints JSON report to stdout
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class EnvSection:
    python_version: str = ""
    venv_active: bool = False
    torch_available: bool = False
    cuda_available: bool = False
    cuda_device: str | None = None
    disk_free_gib: float = 0.0
    ram_free_gib: float = 0.0
    cpu_cores: int = 0
    packages: dict[str, str] = field(default_factory=dict)
    missing_packages: list[str] = field(default_factory=list)


@dataclass
class RpcSection:
    rpc_url: str = ""
    reachable: bool = False
    discover_ok: bool = False
    server_version: str = ""
    required_capabilities: dict[str, bool] = field(default_factory=dict)
    param_encoding: dict[str, str] = field(default_factory=dict)
    error: str | None = None


@dataclass
class DASection:
    enabled: bool = False
    status_ok: bool = False
    writable: bool = False
    allow_remote_put: bool = False
    ingest_available: bool = False
    ingest_dir: str | None = None
    error: str | None = None


@dataclass
class StudioSection:
    config_path: str = ""
    logs_dir: str = ""
    profiles_count: int = 0
    issues: list[str] = field(default_factory=list)


@dataclass
class EnaSection:
    datasets_present: bool = False
    dataset_dir: str = ""
    tokenizer_present: bool = False
    local_model_store: list[str] = field(default_factory=list)
    inference_ready: bool = False
    issues: list[str] = field(default_factory=list)


@dataclass
class PipelineSection:
    can_bootstrap_dataset: bool = False
    can_train: bool = False
    can_checkpoint: bool = False
    can_publish_to_da: bool = False
    can_create_pointer: bool = False
    can_sync_from_da: bool = False
    can_run_inference: bool = False
    blockers: list[str] = field(default_factory=list)


@dataclass
class DoctorReport:
    timestamp: str = ""
    duration_ms: int = 0
    environment: EnvSection = field(default_factory=EnvSection)
    node_rpc: RpcSection = field(default_factory=RpcSection)
    da: DASection = field(default_factory=DASection)
    studio: StudioSection = field(default_factory=StudioSection)
    ena: EnaSection = field(default_factory=EnaSection)
    pipeline: PipelineSection = field(default_factory=PipelineSection)
    overall: str = "unknown"  # "ok", "degraded", "error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


# ---------------------------------------------------------------------------
# Required package list
# ---------------------------------------------------------------------------

_REQUIRED_PACKAGES: dict[str, str] = {
    "requests": "requests",
    "PySide6": "PySide6",
}

_OPTIONAL_PACKAGES: dict[str, str] = {
    "torch": "torch",
    "psutil": "psutil",
    "cbor2": "cbor2",
}

_REQUIRED_RPC_CAPABILITIES = [
    "chain.getHead",
    "tx.getTransactionReceipt",
    "da.getStatus",
    "da.putBlob",
    "da.getBlob",
]


# ---------------------------------------------------------------------------
# Section probes
# ---------------------------------------------------------------------------


def _probe_environment() -> EnvSection:
    sec = EnvSection()
    sec.python_version = sys.version.split()[0]
    sec.venv_active = sys.prefix != sys.base_prefix or os.environ.get("VIRTUAL_ENV", "") != ""

    # PyTorch
    try:
        import torch  # type: ignore[import-not-found]
        sec.torch_available = True
        sec.cuda_available = torch.cuda.is_available()
        if sec.cuda_available:
            sec.cuda_device = torch.cuda.get_device_name(0)
    except ImportError:
        pass

    # Disk / RAM / CPU
    try:
        stat = shutil.disk_usage(Path.home())
        sec.disk_free_gib = round(stat.free / 1024 ** 3, 2)
    except Exception:
        pass

    try:
        import psutil  # type: ignore[import-not-found]
        vm = psutil.virtual_memory()
        sec.ram_free_gib = round(vm.available / 1024 ** 3, 2)
        sec.cpu_cores = psutil.cpu_count(logical=False) or os.cpu_count() or 1
    except Exception:
        sec.ram_free_gib = 0.0
        sec.cpu_cores = os.cpu_count() or 1

    # Package versions
    for label, pkg in {**_REQUIRED_PACKAGES, **_OPTIONAL_PACKAGES}.items():
        try:
            mod = importlib.import_module(pkg.split(".")[0])
            ver = getattr(mod, "__version__", "present")
            sec.packages[label] = str(ver)
        except ImportError:
            sec.packages[label] = "missing"

    sec.missing_packages = [
        label for label, pkg in _REQUIRED_PACKAGES.items()
        if sec.packages.get(label, "missing") == "missing"
    ]

    return sec


def _probe_rpc(rpc_url: str) -> RpcSection:
    sec = RpcSection(rpc_url=rpc_url)
    if not rpc_url:
        sec.error = "No RPC URL configured"
        return sec

    try:
        from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

        client = RpcClient(rpc_url, connect_timeout=3.0, read_timeout=8.0)
        # Basic reachability
        try:
            head = client.get_head()
            sec.reachable = True
        except Exception as exc:
            sec.reachable = False
            sec.error = str(exc)

        # Discover
        try:
            registry = client.registry()
            sec.discover_ok = True
            info = registry.server_info or {}
            sec.server_version = str(info.get("version") or "")
            # Check required capabilities
            for cap in _REQUIRED_RPC_CAPABILITIES:
                # Normalise: chain.getHead → chain_getHead
                alt = cap.replace(".", "_")
                found = cap in registry.exact_methods or alt in registry.exact_methods
                sec.required_capabilities[cap] = found
            # Param encoding hints
            from animica_studio.services.rpc_client import _PARAM_ENCODING_BY_URL  # noqa: PLC0415
            sec.param_encoding = dict(_PARAM_ENCODING_BY_URL.get(rpc_url, {}))
        except Exception as exc:
            sec.discover_ok = False
            if sec.error is None:
                sec.error = f"discover failed: {exc}"
    except ImportError:
        sec.error = "RpcClient unavailable (PySide6/requests not installed)"

    return sec


def _probe_da(rpc_url: str) -> DASection:
    sec = DASection()
    if not rpc_url:
        return sec

    try:
        from animica_studio.services.da_client import DaClient  # noqa: PLC0415

        da = DaClient(rpc_url)
        status = da.get_status()
        sec.status_ok = True
        sec.enabled = bool(status.get("enabled"))
        sec.writable = bool(status.get("writable"))
        sec.allow_remote_put = bool(status.get("allow_remote_put"))

        # Ingest dir
        try:
            info = da.get_ingest_dir()
            sec.ingest_dir = str(info.get("dir") or "")
            sec.ingest_available = bool(sec.ingest_dir)
        except Exception:
            sec.ingest_available = False

    except Exception as exc:
        sec.error = str(exc)

    return sec


def _probe_studio() -> StudioSection:
    sec = StudioSection()
    try:
        from animica_studio.util.paths import app_data_dir, logs_dir  # noqa: PLC0415
        from animica_studio.storage.config import load_config  # noqa: PLC0415

        cfg = load_config()
        sec.config_path = str(app_data_dir() / "config.json")
        sec.logs_dir = str(logs_dir())
        sec.profiles_count = len(cfg.rpc_profiles or [])
    except Exception as exc:
        sec.issues.append(f"config load failed: {exc}")

    return sec


def _probe_ena() -> EnaSection:
    sec = EnaSection()
    try:
        from animica_studio.util.paths import app_data_dir  # noqa: PLC0415

        data = app_data_dir()
        dataset_dir = data / "datasets"
        sec.dataset_dir = str(dataset_dir)
        sec.datasets_present = dataset_dir.exists() and any(dataset_dir.iterdir())

        # Local model store
        model_dir = data / "models"
        if model_dir.exists():
            sec.local_model_store = [p.name for p in model_dir.iterdir() if p.is_dir()]

        # Tokenizer
        tok_path = data / "tokenizer"
        sec.tokenizer_present = tok_path.exists()

        # Inference check
        try:
            from animica_studio.services.ena_inference_service import EnaInferenceService  # noqa: PLC0415
            svc = EnaInferenceService.__new__(EnaInferenceService)
            sec.inference_ready = hasattr(svc, "run") or hasattr(svc, "generate")
        except Exception:
            sec.inference_ready = False

    except Exception as exc:
        sec.issues.append(str(exc))

    return sec


def _probe_pipeline(env: EnvSection, rpc: RpcSection, da: DASection, ena: EnaSection) -> PipelineSection:
    sec = PipelineSection()

    # Dataset bootstrap only needs disk space and required packages; torch not required
    sec.can_bootstrap_dataset = len(env.missing_packages) == 0 and env.disk_free_gib > 1.0
    if not sec.can_bootstrap_dataset:
        if env.missing_packages:
            sec.blockers.append(f"missing packages: {env.missing_packages}")
        if env.disk_free_gib <= 1.0:
            sec.blockers.append("disk < 1 GiB free; cannot bootstrap dataset")

    sec.can_train = env.torch_available
    if not env.torch_available:
        sec.blockers.append("torch not installed; cannot train")

    sec.can_checkpoint = env.torch_available and env.disk_free_gib > 1.0
    if env.torch_available and env.disk_free_gib < 1.0:
        sec.blockers.append("disk < 1 GiB free; cannot checkpoint")

    sec.can_publish_to_da = da.enabled and da.writable
    if not da.enabled:
        sec.blockers.append("DA not enabled on node")
    elif not da.writable:
        sec.blockers.append("DA not writable")

    sec.can_create_pointer = sec.can_publish_to_da
    sec.can_sync_from_da = rpc.reachable and da.enabled

    sec.can_run_inference = ena.inference_ready or env.torch_available

    return sec


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_doctor(rpc_url: str = "", verbose: bool = False) -> DoctorReport:
    """Run all probes and return a DoctorReport.

    Parameters
    ----------
    rpc_url:
        Optional override; if empty, loaded from config.
    verbose:
        If True, emit progress lines to stderr.
    """
    t0 = time.monotonic()

    def _vlog(msg: str) -> None:
        if verbose:
            print(f"[doctor] {msg}", file=sys.stderr)

    report = DoctorReport(timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    _vlog("probing environment…")
    report.environment = _probe_environment()

    _vlog("loading config…")
    if not rpc_url:
        try:
            from animica_studio.storage.config import load_config  # noqa: PLC0415
            cfg = load_config()
            profile = cfg.get_active_profile() if hasattr(cfg, "get_active_profile") else None
            if profile:
                rpc_url = profile.node.rpc_local_url or ""
        except Exception:
            pass

    _vlog(f"probing RPC ({rpc_url or 'no URL'})…")
    report.node_rpc = _probe_rpc(rpc_url)

    _vlog("probing DA…")
    report.da = _probe_da(rpc_url)

    _vlog("probing Studio config…")
    report.studio = _probe_studio()

    _vlog("probing ENA/ML…")
    report.ena = _probe_ena()

    _vlog("evaluating pipeline readiness…")
    report.pipeline = _probe_pipeline(
        report.environment, report.node_rpc, report.da, report.ena
    )

    report.duration_ms = int((time.monotonic() - t0) * 1000)

    # Overall status
    all_blockers = list(report.pipeline.blockers)
    if not report.node_rpc.reachable and rpc_url:
        all_blockers.append("RPC unreachable")
    if report.environment.missing_packages:
        all_blockers.append(f"missing packages: {report.environment.missing_packages}")

    if not all_blockers:
        report.overall = "ok"
    elif any("cannot train" in b or "not installed" in b for b in all_blockers):
        report.overall = "degraded"
    else:
        report.overall = "ok"

    return report


def print_report(report: DoctorReport, as_json: bool = False) -> None:
    if as_json:
        print(report.to_json())
        return

    env = report.environment
    rpc = report.node_rpc
    da = report.da
    pipeline = report.pipeline

    print(f"Animica Studio Doctor  ({report.timestamp})")
    print(f"Overall: {report.overall.upper()}")
    print()
    print("=== Environment ===")
    print(f"  Python:      {env.python_version}")
    print(f"  venv:        {'yes' if env.venv_active else 'no'}")
    print(f"  PyTorch:     {'yes' if env.torch_available else 'no'}")
    if env.torch_available:
        print(f"  CUDA:        {'yes (' + (env.cuda_device or '') + ')' if env.cuda_available else 'no'}")
    print(f"  Disk free:   {env.disk_free_gib} GiB")
    print(f"  RAM free:    {env.ram_free_gib} GiB")
    print(f"  CPU cores:   {env.cpu_cores}")
    for pkg, ver in env.packages.items():
        marker = "✓" if ver != "missing" else "✗"
        print(f"  {marker} {pkg}: {ver}")

    print()
    print("=== Node RPC ===")
    print(f"  URL:         {rpc.rpc_url or '(none)'}")
    print(f"  Reachable:   {'yes' if rpc.reachable else 'no'}")
    if rpc.error:
        print(f"  Error:       {rpc.error}")
    if rpc.discover_ok:
        print(f"  Discover:    ok (server {rpc.server_version or 'unknown'})")
        for cap, present in rpc.required_capabilities.items():
            marker = "✓" if present else "✗"
            print(f"  {marker} {cap}")

    print()
    print("=== DA ===")
    print(f"  Enabled:     {'yes' if da.enabled else 'no'}")
    print(f"  Writable:    {'yes' if da.writable else 'no'}")
    print(f"  RemotePut:   {'yes' if da.allow_remote_put else 'no'}")
    print(f"  Ingest:      {'yes (' + str(da.ingest_dir) + ')' if da.ingest_available else 'no'}")
    if da.error:
        print(f"  Error:       {da.error}")

    print()
    print("=== One-click Pipeline Readiness ===")
    checks = [
        ("Bootstrap dataset", pipeline.can_bootstrap_dataset),
        ("Train", pipeline.can_train),
        ("Checkpoint", pipeline.can_checkpoint),
        ("Publish to DA", pipeline.can_publish_to_da),
        ("Create pointer", pipeline.can_create_pointer),
        ("Sync from DA", pipeline.can_sync_from_da),
        ("Run inference", pipeline.can_run_inference),
    ]
    for label, ok in checks:
        marker = "✓" if ok else "✗"
        print(f"  {marker} {label}")
    if pipeline.blockers:
        print()
        print("  Blockers:")
        for b in pipeline.blockers:
            print(f"    - {b}")

    print()
    print(f"Done in {report.duration_ms} ms.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def doctor_main(argv: list[str] | None = None) -> int:
    """Entry point for ``animica-studio doctor``."""
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="animica-studio doctor",
        description="Run a structured health check of the Animica Studio environment.",
    )
    parser.add_argument("--json", action="store_true", help="Output report as JSON")
    parser.add_argument("--rpc-url", default="", help="Override RPC URL to probe")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show progress during check")
    args = parser.parse_args(argv)

    report = run_doctor(rpc_url=args.rpc_url, verbose=args.verbose)
    print_report(report, as_json=args.json)

    # Return exit code: 0 = ok, 1 = degraded/error
    return 0 if report.overall == "ok" else 1
