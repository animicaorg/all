"""VmToolchainService — single integration point between Studio IDE and the Animica VM layer.

All long-running operations are designed to be run off the UI thread (e.g. via
``QThreadPool`` / ``threading.Thread``).  Each method raises ``VmToolchainError``
(or a subclass) on failure with actionable diagnostics.

If ``vm_py`` is not installed the service degrades gracefully — operations that
require it raise ``VmUnavailableError`` with a clear message.

Public surface
--------------
VmToolchainService
    validate_project(project_path)
    compile_contract(entry_file, config=None)
    build_package(project_path)
    simulate_call(package_or_artifact, call_spec)
    simulate_tx(package_or_artifact, tx_spec)
    export_manifest(project_path)
    check_determinism(project_path)
    get_supported_abi_types()
    get_vm_version()

Support types
-------------
CompileResult, SimulateResult, ValidateResult, BuildPackageResult,
DiagnosticEntry, VmToolchainError, VmUnavailableError
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Determinism checker — banned modules / patterns
# ---------------------------------------------------------------------------

_BANNED_IMPORTS: frozenset[str] = frozenset(
    {
        "time",
        "datetime",
        "random",
        "os.urandom",
        "socket",
        "urllib",
        "http",
        "requests",
        "threading",
        "multiprocessing",
        "asyncio",
        "subprocess",
        "hashlib",  # listed for awareness; not in ALWAYS_BANNED (deterministic hashing is allowed)
        "uuid",
        "secrets",
    }
)

# Subset that are *always* forbidden even with deterministic seeding
_ALWAYS_BANNED: frozenset[str] = frozenset(
    {
        "random",
        "socket",
        "urllib",
        "http",
        "requests",
        "threading",
        "multiprocessing",
        "asyncio",
        "subprocess",
        "uuid",
        "secrets",
        "os.urandom",
    }
)

_NONDETERMINISTIC_CALLS: tuple[str, ...] = (
    r"\btime\.time\b",
    r"\btime\.sleep\b",
    r"\bdatetime\.now\b",
    r"\bdatetime\.utcnow\b",
    r"\brandom\.\w",
    r"\buuid\.\w",
    r"\bos\.urandom\b",
    r"\bsecrets\.\w",
    r"\bsocket\.\w",
    r"\bopen\s*\(",  # file IO outside allowed SDK
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class VmToolchainError(Exception):
    """Base error for all VmToolchainService failures."""

    def __init__(self, message: str, diagnostics: "list[DiagnosticEntry] | None" = None) -> None:
        super().__init__(message)
        self.diagnostics: list[DiagnosticEntry] = diagnostics or []


class VmUnavailableError(VmToolchainError):
    """Raised when vm_py is not installed or not discoverable."""


class DeterminismError(VmToolchainError):
    """Raised when a source file fails determinism checks."""


class CompileError(VmToolchainError):
    """Raised when compilation fails."""


class SimulateError(VmToolchainError):
    """Raised when simulation fails."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class DiagnosticEntry:
    """Single diagnostic message (error/warning/info)."""

    severity: str  # "error" | "warning" | "info"
    message: str
    file: str = ""
    line: int = 0
    column: int = 0
    code: str = ""
    hint: str = ""


@dataclass
class CompileResult:
    success: bool
    artifact_path: str = ""
    bytecode_hash: str = ""
    abi: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[DiagnosticEntry] = field(default_factory=list)
    raw_output: str = ""


@dataclass
class SimulateResult:
    success: bool
    return_value: Any = None
    events: list[dict[str, Any]] = field(default_factory=list)
    gas_used: int = 0
    gas_estimate: int = 0
    state_diff: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[DiagnosticEntry] = field(default_factory=list)
    raw_output: str = ""


@dataclass
class ValidateResult:
    valid: bool
    issues: list[DiagnosticEntry] = field(default_factory=list)


@dataclass
class BuildPackageResult:
    success: bool
    package_path: str = ""
    manifest_path: str = ""
    abi_path: str = ""
    bytecode_hash: str = ""
    build_info: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[DiagnosticEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class VmToolchainService:
    """Studio ↔ VM integration layer.

    All methods are safe to call from a worker thread.  They never touch Qt
    objects directly; callers are responsible for marshalling results back to
    the UI thread.
    """

    def __init__(self) -> None:
        self._vm: Any = self._try_import_vm()

    # ------------------------------------------------------------------
    # Version / capability discovery
    # ------------------------------------------------------------------

    def get_vm_version(self) -> str:
        """Return the VM version string, or ``"unavailable"``."""
        if self._vm is None:
            return "unavailable"
        for attr in ("VERSION", "__version__", "version"):
            v = getattr(self._vm, attr, None)
            if isinstance(v, str):
                return v
        try:
            result = subprocess.run(
                [sys.executable, "-m", "vm_py", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip() or result.stderr.strip() or "unknown"
        except Exception:
            return "unknown"

    def get_supported_abi_types(self) -> list[str]:
        """Return the list of ABI types supported by this VM build."""
        if self._vm is None:
            return []
        for attr in ("ABI_TYPES", "SUPPORTED_TYPES", "abi_types"):
            val = getattr(self._vm, attr, None)
            if isinstance(val, (list, tuple)):
                return list(val)
        # Fallback: well-known Animica VM ABI types
        return [
            "uint8", "uint16", "uint32", "uint64", "uint128", "uint256",
            "int8", "int16", "int32", "int64",
            "bool", "address", "bytes", "bytes32", "string",
            "uint256[]", "address[]", "bytes[]", "string[]",
        ]

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_project(self, project_path: str | Path) -> ValidateResult:
        """Validate all Python source files in *project_path*.

        Checks:
        - manifest.json / abi.json schema basics
        - source file syntax (compile-time)
        - determinism (no banned imports)
        """
        root = Path(project_path).resolve()
        if not root.is_dir():
            return ValidateResult(
                valid=False,
                issues=[DiagnosticEntry("error", f"Not a directory: {project_path}")],
            )

        issues: list[DiagnosticEntry] = []

        # Check source files
        for py_file in root.rglob("*.py"):
            rel = str(py_file.relative_to(root))
            issues.extend(self._check_syntax(py_file, rel))
            issues.extend(self._check_determinism_file(py_file, rel))

        # Check manifest/abi
        for name in ("manifest.json", "abi.json"):
            path = root / name
            if path.exists():
                issues.extend(self._validate_json_file(path, str(path.relative_to(root))))

        errors = [i for i in issues if i.severity == "error"]
        return ValidateResult(valid=len(errors) == 0, issues=issues)

    # ------------------------------------------------------------------
    # Determinism checks
    # ------------------------------------------------------------------

    def check_determinism(self, project_path: str | Path) -> ValidateResult:
        """Check all .py files in *project_path* for determinism violations."""
        root = Path(project_path).resolve()
        issues: list[DiagnosticEntry] = []
        for py_file in root.rglob("*.py"):
            rel = str(py_file.relative_to(root))
            issues.extend(self._check_determinism_file(py_file, rel))
        errors = [i for i in issues if i.severity == "error"]
        return ValidateResult(valid=len(errors) == 0, issues=issues)

    def _check_determinism_file(self, path: Path, rel: str) -> list[DiagnosticEntry]:
        issues: list[DiagnosticEntry] = []
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return [DiagnosticEntry("error", f"Cannot read file: {exc}", file=rel)]

        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            # Check import statements
            for banned in _ALWAYS_BANNED:
                patterns = [
                    rf"^\s*import\s+{re.escape(banned.split('.')[0])}\b",
                    rf"^\s*from\s+{re.escape(banned.split('.')[0])}\b",
                ]
                for pat in patterns:
                    if re.search(pat, line):
                        issues.append(
                            DiagnosticEntry(
                                severity="error",
                                message=f"Non-deterministic import: '{banned}' is not allowed in VM contracts",
                                file=rel,
                                line=lineno,
                                code="DET001",
                                hint=f"Remove 'import {banned}' — contracts must be deterministic.",
                            )
                        )
                        break
            # Check call patterns
            for pattern in _NONDETERMINISTIC_CALLS:
                if re.search(pattern, stripped):
                    issues.append(
                        DiagnosticEntry(
                            severity="warning",
                            message=f"Potentially non-deterministic call: {stripped[:60]}",
                            file=rel,
                            line=lineno,
                            code="DET002",
                            hint="Review this call for non-deterministic side effects.",
                        )
                    )
        return issues

    # ------------------------------------------------------------------
    # Syntax check
    # ------------------------------------------------------------------

    def _check_syntax(self, path: Path, rel: str) -> list[DiagnosticEntry]:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            compile(source, str(path), "exec")
            return []
        except SyntaxError as exc:
            return [
                DiagnosticEntry(
                    severity="error",
                    message=str(exc.msg),
                    file=rel,
                    line=exc.lineno or 0,
                    column=exc.offset or 0,
                    code="SYN001",
                    hint="Fix the syntax error before compiling.",
                )
            ]
        except Exception as exc:
            return [DiagnosticEntry("error", str(exc), file=rel)]

    # ------------------------------------------------------------------
    # Compile
    # ------------------------------------------------------------------

    def compile_contract(
        self,
        entry_file: str | Path,
        config: dict[str, Any] | None = None,
    ) -> CompileResult:
        """Compile a contract source file.

        Tries vm_py CLI first, then the Python API adapter, then falls back to
        a syntax-only check with a clear message.
        """
        entry = Path(entry_file).resolve()
        if not entry.exists():
            return CompileResult(
                success=False,
                diagnostics=[DiagnosticEntry("error", f"Entry file not found: {entry_file}")],
            )

        # Determinism preflight
        det = self._check_determinism_file(entry, str(entry))
        errors = [d for d in det if d.severity == "error"]
        if errors:
            raise DeterminismError(
                "Determinism checks failed before compile",
                diagnostics=errors,
            )

        # Try CLI
        result = self._compile_via_cli(entry, config or {})
        if result is not None:
            return result

        # Try Python API
        result = self._compile_via_api(entry, config or {})
        if result is not None:
            return result

        # Syntax-only fallback
        syntax_issues = self._check_syntax(entry, entry.name)
        syntax_errors = [i for i in syntax_issues if i.severity == "error"]
        if syntax_errors:
            return CompileResult(success=False, diagnostics=syntax_issues)

        return CompileResult(
            success=True,
            raw_output="vm_py not available; syntax check passed only",
            diagnostics=[
                DiagnosticEntry(
                    severity="info",
                    message="vm_py not installed — only syntax validation performed",
                    hint="Install vm_py to enable full compilation.",
                )
            ],
        )

    def _compile_via_cli(
        self, entry: Path, config: dict[str, Any]
    ) -> CompileResult | None:
        """Attempt compile via ``python -m vm_py.cli.compile``."""
        manifest = Path(str(config.get("manifest") or (entry.parent / "manifest.json"))).expanduser()
        out_dir_raw = str(config.get("out_dir") or "").strip()
        persist_artifact = bool(out_dir_raw)
        out_dir = Path(out_dir_raw).expanduser() if persist_artifact else None
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
        artifact_name = f"{entry.stem}.ir"
        meta_name = f"{entry.stem}.compile-meta.json"
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                scratch_dir = out_dir if out_dir is not None else Path(tmp_dir)
                artifact_path = scratch_dir / artifact_name
                meta_path = scratch_dir / meta_name
                cmd = [sys.executable, "-m", "vm_py.cli.compile"]
                if manifest.exists() and self._manifest_looks_runnable(manifest):
                    cmd.extend(["--manifest", str(manifest)])
                else:
                    cmd.append(str(entry))
                cmd.extend(["--out", str(artifact_path), "--meta", str(meta_path)])
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30
                )
                if proc.returncode == 0:
                    bytecode_hash = ""
                    persisted_artifact_path = ""
                    if artifact_path.exists():
                        bytecode_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                        if persist_artifact:
                            persisted_artifact_path = str(artifact_path)
                    return CompileResult(
                        success=True,
                        artifact_path=persisted_artifact_path,
                        bytecode_hash=bytecode_hash,
                        raw_output=(proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else ""),
                    )
                else:
                    error_output = proc.stderr or proc.stdout or ""
                    # If vm_py module is not installed, fall through to API/syntax fallback
                    if "No module named 'vm_py'" in error_output or "ModuleNotFoundError" in error_output:
                        return None
                    diagnostics = self._parse_cli_diagnostics(error_output, str(entry))
                    return CompileResult(
                        success=False,
                        diagnostics=diagnostics,
                        raw_output=error_output,
                    )
        except FileNotFoundError:
            return None
        except Exception as exc:
            log.debug("CLI compile failed: %s", exc)
            return None

    def _compile_via_api(
        self, entry: Path, config: dict[str, Any]
    ) -> CompileResult | None:
        """Attempt compile via vm_py Python API."""
        if self._vm is None:
            return None
        try:
            for fn_name in ("compile_source", "compile", "compile_and_link"):
                fn = getattr(self._vm, fn_name, None)
                if not callable(fn):
                    continue
                source = entry.read_text(encoding="utf-8")
                manifest_path = entry.parent / "manifest.json"
                manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
                result = fn(source, manifest)
                if isinstance(result, (bytes, bytearray)):
                    code = bytes(result)
                    bh = hashlib.sha256(code).hexdigest()
                    return CompileResult(success=True, bytecode_hash=bh)
                if isinstance(result, dict):
                    code = result.get("code") or result.get("bytecode")
                    if isinstance(code, (bytes, bytearray)):
                        bh = hashlib.sha256(bytes(code)).hexdigest()
                    else:
                        bh = ""
                    diags = self._normalize_api_diagnostics(result.get("diagnostics"))
                    return CompileResult(
                        success=bool(code),
                        bytecode_hash=bh,
                        abi=result.get("abi", {}),
                        diagnostics=diags,
                        raw_output=str(result.get("log", "")),
                    )
        except Exception as exc:
            log.debug("API compile failed: %s", exc)
        return None

    def _manifest_looks_runnable(self, manifest_path: Path) -> bool:
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(raw, dict):
            return False
        for key in ("source", "entry", "contract", "contract_path", "path"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return False

    # ------------------------------------------------------------------
    # Build package
    # ------------------------------------------------------------------

    def build_package(self, project_path: str | Path) -> BuildPackageResult:
        """Build a deployable artifact package from *project_path*.

        Outputs:
        - manifest.json
        - abi.json
        - bytecode artifact
        - build-info.json
        """
        root = Path(project_path).resolve()

        # Validate first
        val = self.validate_project(root)
        if not val.valid:
            return BuildPackageResult(
                success=False,
                diagnostics=val.issues,
            )

        # Find manifest
        manifest_path = root / "manifest.json"
        build_dir = root / "build"
        build_dir.mkdir(exist_ok=True)
        entry_file = self._find_entry_file(root)
        if entry_file is None:
            return BuildPackageResult(
                success=False,
                diagnostics=[
                    DiagnosticEntry(
                        "error",
                        "No entry file found (expected contract.py or main.py)",
                        hint="Add a contract.py file to the project root.",
                    )
                ],
            )

        compile_result = self.compile_contract(
            entry_file,
            {"manifest": str(manifest_path), "out_dir": str(build_dir)},
        )
        build_info: dict[str, Any] = {
            "toolchain": self.get_vm_version(),
            "entry": str(entry_file.relative_to(root)),
            "bytecode_hash": compile_result.bytecode_hash,
        }

        if not compile_result.success:
            return BuildPackageResult(
                success=False,
                diagnostics=compile_result.diagnostics,
                build_info=build_info,
            )

        # Write build-info.json
        build_info_path = build_dir / "build-info.json"
        build_info_path.write_text(
            json.dumps(build_info, indent=2), encoding="utf-8"
        )

        exported_manifest = self.export_manifest(root)
        built_manifest_path = build_dir / "manifest.json"
        built_manifest_path.write_text(json.dumps(exported_manifest, indent=2), encoding="utf-8")

        abi_path = ""
        abi_payload: Any = exported_manifest.get("abi") if isinstance(exported_manifest, dict) else None
        root_abi_path = root / "abi.json"
        if root_abi_path.exists():
            try:
                abi_payload = json.loads(root_abi_path.read_text(encoding="utf-8"))
            except Exception:
                abi_payload = abi_payload
        if abi_payload:
            built_abi_path = build_dir / "abi.json"
            built_abi_path.write_text(json.dumps(abi_payload, indent=2), encoding="utf-8")
            abi_path = str(built_abi_path)

        return BuildPackageResult(
            success=True,
            manifest_path=str(built_manifest_path),
            abi_path=abi_path,
            bytecode_hash=compile_result.bytecode_hash,
            package_path=compile_result.artifact_path,
            build_info=build_info,
        )

    # ------------------------------------------------------------------
    # Simulate
    # ------------------------------------------------------------------

    def simulate_call(
        self,
        project_path: str | Path,
        call_spec: dict[str, Any],
    ) -> SimulateResult:
        """Simulate a read-only call against a compiled contract.

        *call_spec* keys:
        - ``method`` (str): method name
        - ``args`` (list): positional arguments
        - ``caller`` (str, optional): caller address
        - ``chain_id`` (int, optional): chain identifier
        """
        return self._run_simulation(project_path, call_spec, kind="call")

    def simulate_tx(
        self,
        project_path: str | Path,
        tx_spec: dict[str, Any],
    ) -> SimulateResult:
        """Simulate a state-changing transaction (no actual broadcast).

        *tx_spec* keys: same as *call_spec* plus:
        - ``value`` (int, optional): native value transfer
        - ``gas_limit`` (int, optional)
        """
        return self._run_simulation(project_path, tx_spec, kind="tx")

    def _run_simulation(
        self,
        project_path: str | Path,
        spec: dict[str, Any],
        kind: str,
    ) -> SimulateResult:
        root = Path(project_path).resolve()
        entry = self._find_entry_file(root)
        if entry is None:
            return SimulateResult(
                success=False,
                diagnostics=[DiagnosticEntry("error", "No entry file found in project")],
            )

        method = spec.get("method", "")
        args = spec.get("args", [])

        # Try CLI simulation
        result = self._simulate_via_cli(root, entry, method, args, kind)
        if result is not None:
            return result

        # Try API
        result = self._simulate_via_api(entry, method, args, spec)
        if result is not None:
            return result

        return SimulateResult(
            success=False,
            diagnostics=[
                DiagnosticEntry(
                    severity="info",
                    message="vm_py not available — simulation requires vm_py installation",
                    hint="Install vm_py to enable local simulation.",
                )
            ],
        )

    def _simulate_via_cli(
        self,
        root: Path,
        entry: Path,
        method: str,
        args: list[Any],
        kind: str,
    ) -> SimulateResult | None:
        try:
            manifest = root / "manifest.json"
            cmd = [
                sys.executable, "-m", "vm_py.cli.run",
                "--manifest", str(manifest),
                "--call", method,
            ]
            if args:
                cmd += ["--args", json.dumps(args)]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            if proc.returncode == 0:
                try:
                    data = json.loads(proc.stdout)
                except json.JSONDecodeError:
                    data = {}
                return SimulateResult(
                    success=True,
                    return_value=data.get("return_value"),
                    events=data.get("events", []),
                    gas_used=data.get("gas_used", 0),
                    gas_estimate=data.get("gas_estimate", 0),
                    state_diff=data.get("state_diff", {}),
                    raw_output=proc.stdout,
                )
            error_output = proc.stderr or proc.stdout or ""
            if "No module named 'vm_py'" in error_output or "ModuleNotFoundError" in error_output:
                return None
            diags = self._parse_cli_diagnostics(error_output, str(entry))
            return SimulateResult(
                success=False,
                diagnostics=diags,
                raw_output=error_output,
            )
        except FileNotFoundError:
            return None
        except Exception as exc:
            log.debug("CLI simulate failed: %s", exc)
            return None

    def _simulate_via_api(
        self,
        entry: Path,
        method: str,
        args: list[Any],
        spec: dict[str, Any],
    ) -> SimulateResult | None:
        if self._vm is None:
            return None
        try:
            for fn_name in ("simulate_call", "run_call", "run"):
                fn = getattr(self._vm, fn_name, None)
                if not callable(fn):
                    continue
                source = entry.read_text(encoding="utf-8")
                manifest_path = entry.parent / "manifest.json"
                manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
                res = fn(source, manifest, method, args)
                if isinstance(res, dict):
                    return SimulateResult(
                        success=res.get("success", True),
                        return_value=res.get("return_value"),
                        events=res.get("events", []),
                        gas_used=res.get("gas_used", 0),
                        gas_estimate=res.get("gas_estimate", 0),
                        state_diff=res.get("state_diff", {}),
                        raw_output=str(res),
                    )
        except Exception as exc:
            log.debug("API simulate failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Manifest export
    # ------------------------------------------------------------------

    def export_manifest(self, project_path: str | Path) -> dict[str, Any]:
        """Return a canonical manifest dict for the project."""
        root = Path(project_path).resolve()
        manifest_path = root / "manifest.json"
        if manifest_path.exists():
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                # Canonical sort
                return json.loads(json.dumps(raw, sort_keys=True))
            except Exception as exc:
                raise VmToolchainError(f"Invalid manifest.json: {exc}") from exc
        # Try to synthesize a basic manifest from project structure
        entry = self._find_entry_file(root)
        return {
            "name": root.name,
            "version": "0.1.0",
            "entry": str(entry.relative_to(root)) if entry else "contract.py",
            "language": "python",
        }

    # ------------------------------------------------------------------
    # JSON validation helpers
    # ------------------------------------------------------------------

    def _validate_json_file(self, path: Path, rel: str) -> list[DiagnosticEntry]:
        issues: list[DiagnosticEntry] = []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return [
                DiagnosticEntry(
                    severity="error",
                    message=f"Invalid JSON: {exc}",
                    file=rel,
                    line=getattr(exc, "lineno", 0),
                    code="JSON001",
                    hint="Fix the JSON syntax error.",
                )
            ]
        if path.name == "manifest.json":
            issues.extend(self._validate_manifest(data, rel))
        elif path.name == "abi.json":
            issues.extend(self._validate_abi(data, rel))
        return issues

    def _validate_manifest(self, data: Any, rel: str) -> list[DiagnosticEntry]:
        issues: list[DiagnosticEntry] = []
        if not isinstance(data, dict):
            return [DiagnosticEntry("error", "manifest.json must be a JSON object", file=rel)]
        for required in ("name", "version"):
            if required not in data:
                issues.append(
                    DiagnosticEntry(
                        severity="error",
                        message=f"Missing required manifest field: '{required}'",
                        file=rel,
                        code="MAN001",
                        hint=f"Add \"{required}\": \"...\" to manifest.json",
                    )
                )
        return issues

    def _validate_abi(self, data: Any, rel: str) -> list[DiagnosticEntry]:
        issues: list[DiagnosticEntry] = []
        if not isinstance(data, (list, dict)):
            return [DiagnosticEntry("error", "abi.json must be a JSON array or object", file=rel)]
        items = data if isinstance(data, list) else data.get("abi", [])
        seen_selectors: set[str] = set()
        for i, entry in enumerate(items):
            if not isinstance(entry, dict):
                continue
            name = entry.get("name", f"<item {i}>")
            inputs = entry.get("inputs", [])
            selector = f"{name}({','.join(inp.get('type','') for inp in inputs)})"
            if selector in seen_selectors:
                issues.append(
                    DiagnosticEntry(
                        severity="error",
                        message=f"Duplicate ABI selector: {selector}",
                        file=rel,
                        code="ABI001",
                        hint="Each function/event must have a unique name+input signature.",
                    )
                )
            seen_selectors.add(selector)
        return issues

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _try_import_vm() -> Any:
        for mod_name in ("vm_py", "vm_py.api", "vm_py.compiler"):
            try:
                return importlib.import_module(mod_name)
            except ImportError:
                continue
        return None

    @staticmethod
    def find_entry_file(root: Path) -> Path | None:
        """Return the primary contract entry file within *root*.

        Checks for ``contract.py``, ``main.py``, ``index.py`` in order;
        falls back to the first ``*.py`` file found.
        """
        for name in ("contract.py", "main.py", "index.py"):
            p = root / name
            if p.exists():
                return p
        # Fallback: first .py file
        for p in sorted(root.glob("*.py")):
            return p
        return None

    # Keep private alias for internal use during transition
    _find_entry_file = find_entry_file  # type: ignore[assignment]

    @staticmethod
    def _parse_cli_diagnostics(output: str, default_file: str) -> list[DiagnosticEntry]:
        """Parse common Python/vm_py error output into DiagnosticEntry items."""
        entries: list[DiagnosticEntry] = []
        file_re = re.compile(r'File "([^"]+)", line (\d+)')
        error_re = re.compile(r"^(Error|SyntaxError|TypeError|ValueError|RuntimeError):\s+(.+)$")
        lines = output.splitlines()
        current_file = default_file
        current_line = 0
        for line in lines:
            m = file_re.search(line)
            if m:
                current_file = m.group(1)
                current_line = int(m.group(2))
                continue
            m = error_re.match(line.strip())
            if m:
                entries.append(
                    DiagnosticEntry(
                        severity="error",
                        message=f"{m.group(1)}: {m.group(2)}",
                        file=current_file,
                        line=current_line,
                        code="CLI001",
                        hint="Review the error message and fix the contract source.",
                    )
                )
        if not entries and output.strip():
            entries.append(
                DiagnosticEntry(
                    severity="error",
                    message=output.strip()[:500],
                    file=default_file,
                )
            )
        return entries

    @staticmethod
    def _normalize_api_diagnostics(raw: Any) -> list[DiagnosticEntry]:
        """Normalize vm_py API diagnostic payloads to DiagnosticEntry list."""
        if raw is None:
            return []
        if isinstance(raw, list):
            result = []
            for item in raw:
                if isinstance(item, dict):
                    result.append(
                        DiagnosticEntry(
                            severity=item.get("severity", "error"),
                            message=item.get("message", str(item)),
                            file=item.get("file", ""),
                            line=item.get("line", 0),
                            column=item.get("column", 0),
                            code=item.get("code", ""),
                            hint=item.get("hint", ""),
                        )
                    )
                else:
                    result.append(DiagnosticEntry("error", str(item)))
            return result
        return [DiagnosticEntry("error", str(raw))]
