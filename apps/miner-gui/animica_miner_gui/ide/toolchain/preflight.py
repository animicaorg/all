"""Preflight checks for IDE deploy readiness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from animica_miner_gui.backend.rpc_client import RPCClient
from animica_miner_gui.ide.toolchain.builder import BuildResult, build_contract
from animica_miner_gui.ide.toolchain.diagnostics import Diagnostic
from animica_miner_gui.ide.toolchain.manifest import (
    ManifestLoadError,
    load_manifest,
    resolve_abi,
    resolve_manifest_path,
    resolve_source_path,
    validate_manifest,
)
from animica_miner_gui.ide.toolchain.simulator import simulate_call, simulate_tx


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    message: str
    diagnostics: Sequence[Diagnostic] = ()


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    message: str
    diagnostics: Sequence[Diagnostic]
    checks: Sequence[PreflightCheck]


def run_preflight(workspace: Path, rpc_client: Optional[RPCClient]) -> PreflightResult:
    diagnostics: List[Diagnostic] = []
    checks: List[PreflightCheck] = []

    if not workspace or not workspace.exists():
        diagnostics.append(Diagnostic(message="Workspace path is invalid."))
        return PreflightResult(False, "Invalid workspace", diagnostics, checks)

    build_result: BuildResult = build_contract(workspace)
    diagnostics.extend(build_result.diagnostics or [])
    checks.append(
        PreflightCheck(
            name="Build",
            ok=build_result.success,
            message=build_result.message,
            diagnostics=build_result.diagnostics or (),
        )
    )

    manifest_path = resolve_manifest_path(workspace)
    manifest = None
    abi = None
    source_path = None
    if not manifest_path:
        diagnostics.append(Diagnostic(message="manifest.json not found."))
        checks.append(PreflightCheck(name="Manifest", ok=False, message="manifest.json not found."))
    else:
        try:
            manifest = load_manifest(manifest_path)
            source_path = resolve_source_path(manifest, manifest_path)
            abi = resolve_abi(manifest, manifest_path)
            manifest_issues = validate_manifest(manifest, abi)
            for issue in manifest_issues:
                message = issue.message
                if issue.path:
                    message = f"{issue.path}: {message}"
                diagnostics.append(Diagnostic(message=message, severity=issue.severity))
            checks.append(
                PreflightCheck(
                    name="Manifest",
                    ok=all(issue.severity != "error" for issue in manifest_issues),
                    message="Manifest validated" if manifest_issues else "Manifest loaded",
                )
            )
        except ManifestLoadError as exc:
            diagnostics.append(Diagnostic(message=str(exc)))
            checks.append(PreflightCheck(name="Manifest", ok=False, message=str(exc)))

    sim_checks = _run_simulation_checks(manifest, abi, source_path)
    checks.extend(sim_checks)
    for check in sim_checks:
        diagnostics.extend(check.diagnostics)

    rpc_check = _check_rpc(rpc_client)
    checks.append(rpc_check)
    diagnostics.extend(rpc_check.diagnostics)

    ok = all(check.ok for check in checks)
    message = "Preflight completed" if ok else "Preflight found issues"
    return PreflightResult(ok, message, diagnostics, checks)


def _run_simulation_checks(
    manifest: Optional[dict],
    abi: Optional[dict],
    source_path: Optional[Path],
) -> List[PreflightCheck]:
    checks: List[PreflightCheck] = []
    if not manifest or not abi or not source_path:
        diag = Diagnostic(message="Simulation skipped: manifest/ABI unavailable.", severity="warning")
        checks.append(PreflightCheck(name="Simulation (call)", ok=False, message=diag.message, diagnostics=[diag]))
        checks.append(PreflightCheck(name="Simulation (tx)", ok=False, message=diag.message, diagnostics=[diag]))
        return checks

    manifest_with_source = dict(manifest)
    manifest_with_source["source"] = str(source_path)
    manifest_with_source["abi"] = abi
    functions = [fn for fn in abi.get("functions", []) if isinstance(fn, dict)]

    call_fn = _pick_function(functions, {"view", "pure"})
    if call_fn:
        result = simulate_call(manifest_with_source, call_fn["name"], {})
        diag = Diagnostic(
            message=f"Simulate call {call_fn['name']}: {result.message}",
            severity="warning" if not result.ok else "info",
        )
        checks.append(
            PreflightCheck(
                name="Simulation (call)",
                ok=result.ok,
                message=result.message,
                diagnostics=[diag],
            )
        )
    else:
        diag = Diagnostic(
            message="No zero-arg view/pure function found for call simulation.",
            severity="warning",
        )
        checks.append(
            PreflightCheck(
                name="Simulation (call)",
                ok=True,
                message=diag.message,
                diagnostics=[diag],
            )
        )

    tx_fn = _pick_function(functions, {"nonpayable", "payable"})
    if tx_fn:
        result = simulate_tx(manifest_with_source, tx_fn["name"], {}, {})
        diag = Diagnostic(
            message=f"Simulate tx {tx_fn['name']}: {result.message}",
            severity="warning" if not result.ok else "info",
        )
        checks.append(
            PreflightCheck(
                name="Simulation (tx)",
                ok=result.ok,
                message=result.message,
                diagnostics=[diag],
            )
        )
    else:
        diag = Diagnostic(
            message="No zero-arg mutating function found for tx simulation.",
            severity="warning",
        )
        checks.append(
            PreflightCheck(
                name="Simulation (tx)",
                ok=True,
                message=diag.message,
                diagnostics=[diag],
            )
        )

    return checks


def _pick_function(functions: Sequence[dict], mutability: set[str]) -> Optional[dict]:
    for fn in functions:
        state = str(fn.get("stateMutability") or "").lower()
        inputs = fn.get("inputs") or []
        if state in mutability and isinstance(inputs, list) and len(inputs) == 0:
            return fn
    return None


def _check_rpc(rpc_client: Optional[RPCClient]) -> PreflightCheck:
    if rpc_client is None:
        diag = Diagnostic(message="RPC check skipped: no RPC client connected.", severity="warning")
        return PreflightCheck(name="RPC", ok=False, message=diag.message, diagnostics=[diag])
    ok = rpc_client.check_connection()
    if ok:
        diag = Diagnostic(message="RPC reachable.", severity="info")
        return PreflightCheck(name="RPC", ok=True, message="RPC reachable", diagnostics=[diag])
    diag = Diagnostic(message="RPC unreachable or methods unavailable.", severity="error")
    return PreflightCheck(name="RPC", ok=False, message=diag.message, diagnostics=[diag])
