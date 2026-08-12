"""Animica Python Cloud — pre-deploy static validation (§4, §40).

Reads {"source": "...", "entrypoint": "main"} on stdin, writes a JSON report on stdout.

This is a DEFENSE-IN-DEPTH layer, not the security boundary. The boundary is the container:
no network, read-only rootfs, dropped capabilities, cgroup limits. This pass exists to give
the developer fast, precise feedback (with line numbers) and to refuse code whose obvious
purpose is to attack the platform, so we are not relying on the sandbox alone.

It never executes the code — `ast.parse` only.
"""

from __future__ import annotations

import ast
import json
import sys

# Modules that cannot work in the sandbox (no network) or that only make sense as an attack.
# Blocking them turns a confusing runtime failure into a clear deploy-time error.
FORBIDDEN_IMPORTS = {
    "ctypes": "native memory access is not available in the sandbox",
    "multiprocessing": "process pools are not available (pid-capped sandbox)",
    "socket": "the sandbox has no network; use animica.http.fetch instead",
    "socketserver": "the sandbox has no network",
    "http": "the sandbox has no network; use animica.http.fetch instead",
    "urllib": "the sandbox has no network; use animica.http.fetch instead",
    "ftplib": "the sandbox has no network",
    "telnetlib": "the sandbox has no network",
    "smtplib": "the sandbox has no network",
    "asyncio": "async event loops are not supported by the current runtime ABI",
    "pty": "terminal allocation is not available",
    "tty": "terminal allocation is not available",
    "fcntl": "low-level file control is not available",
    "mmap": "shared memory mapping is not available",
    "resource": "resource limits are enforced by the platform and cannot be changed",
    "pdb": "the debugger is not available",
    "distutils": "package installation is not available",
    "setuptools": "package installation is not available",
    "pip": "packages cannot be installed at runtime; declare them at deploy time",
    "requests": "not installed; use animica.http.fetch",
    "httpx": "not installed; use animica.http.fetch",
    "aiohttp": "not installed; use animica.http.fetch",
}

# Allowed even though they touch the OS: read-only introspection the runtime needs.
SUBPROCESS_MODULES = {"subprocess", "pty", "posix", "nt"}

DANGEROUS_CALLS = {
    "eval": "eval() of dynamic source is not allowed",
    "exec": "exec() of dynamic source is not allowed",
    "compile": "compile() of dynamic source is not allowed",
    "__import__": "dynamic __import__ is not allowed; use a normal import statement",
    "breakpoint": "breakpoint() is not available",
}

DANGEROUS_ATTRS = {
    "__subclasses__": "class-hierarchy walking is a known sandbox-escape pattern",
    "__globals__": "reaching into function globals is not allowed",
    "__code__": "code-object manipulation is not allowed",
    "__closure__": "closure introspection is not allowed",
    "__bases__": "class-hierarchy walking is not allowed",
    "__mro__": "class-hierarchy walking is not allowed",
    "f_locals": "frame introspection is not allowed",
    "f_globals": "frame introspection is not allowed",
    "gi_frame": "generator frame introspection is not allowed",
}

OS_DANGEROUS = {
    "system": "os.system() is not allowed",
    "popen": "os.popen() is not allowed",
    "fork": "forking is not allowed",
    "forkpty": "forking is not allowed",
    "execv": "exec* is not allowed",
    "execve": "exec* is not allowed",
    "execl": "exec* is not allowed",
    "execlp": "exec* is not allowed",
    "execvp": "exec* is not allowed",
    "spawnv": "spawn* is not allowed",
    "setuid": "changing uid is not allowed",
    "setgid": "changing gid is not allowed",
}

# Cheap secret-shaped-literal detector (§40). Advisory only — we warn, never block, because a
# false positive must not stop a deploy.
SECRET_HINTS = (
    ("sk-", "an OpenAI-style API key"),
    ("ghp_", "a GitHub token"),
    ("github_pat_", "a GitHub token"),
    ("AKIA", "an AWS access key id"),
    ("-----BEGIN", "a private key block"),
    ("anm_mkt_", "an Animica API key"),
    ("anm_live_", "an Animica API key"),
)


class Finding(dict):
    def __init__(self, severity: str, code: str, message: str, line: int = 0, col: int = 0):
        super().__init__(severity=severity, code=code, message=message, line=line, col=col)


class Visitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.functions: list[str] = []
        self.imports: set[str] = set()
        self.uses_animica = False
        self.capabilities: set[str] = set()

    def err(self, node, code, message):
        self.findings.append(Finding("error", code, message, getattr(node, "lineno", 0), getattr(node, "col_offset", 0)))

    def warn(self, node, code, message):
        self.findings.append(Finding("warning", code, message, getattr(node, "lineno", 0), getattr(node, "col_offset", 0)))

    # --- imports ---------------------------------------------------------

    def _check_module(self, node, name: str):
        root = name.split(".")[0]
        self.imports.add(root)
        if root in FORBIDDEN_IMPORTS:
            self.err(node, "forbidden_import", f"`import {name}` is not allowed: {FORBIDDEN_IMPORTS[root]}")
        elif root in SUBPROCESS_MODULES:
            self.err(node, "forbidden_import", f"`import {name}` is not allowed: running subprocesses is not permitted")
        if root == "animica":
            self.uses_animica = True

    def visit_Import(self, node: ast.Import):
        for a in node.names:
            self._check_module(node, a.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            self._check_module(node, node.module)
        if node.level and node.level > 0:
            self.err(node, "relative_import", "relative imports are not supported — a function is a single module")
        self.generic_visit(node)

    # --- calls / attributes ----------------------------------------------

    def visit_Call(self, node: ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id in DANGEROUS_CALLS:
            self.err(node, "dangerous_call", DANGEROUS_CALLS[fn.id])
        if isinstance(fn, ast.Attribute):
            owner = fn.value
            if isinstance(owner, ast.Name):
                if owner.id == "os" and fn.attr in OS_DANGEROUS:
                    self.err(node, "dangerous_call", OS_DANGEROUS[fn.attr])
                if owner.id == "subprocess":
                    self.err(node, "dangerous_call", "running subprocesses is not permitted")
                # Capability inference, so the UI can show what a function will be able to do.
                if owner.id == "animica":
                    self._infer_cap(fn.attr, node)
            if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name) and owner.value.id in ("animica", "ctx"):
                self._infer_cap(owner.attr, node)
        self.generic_visit(node)

    def _infer_cap(self, attr: str, node):
        mapping = {
            "ai": "AI_INFERENCE",
            "call": "CALL_FUNCTION",
            "wallet": "SPEND_ANM",
            "chain": "READ_CHAIN",
            "state": "PERSIST_STATE",
            "http": "HTTP_FETCH",
        }
        if attr in mapping:
            self.capabilities.add(mapping[attr])

    def visit_Attribute(self, node: ast.Attribute):
        if node.attr in DANGEROUS_ATTRS:
            self.err(node, "dangerous_attribute", DANGEROUS_ATTRS[node.attr])
        self.generic_visit(node)

    # --- definitions ------------------------------------------------------

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.functions.append(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.functions.append(node.name)
        self.err(node, "async_unsupported", f"`async def {node.name}` is not supported by the current runtime ABI")
        self.generic_visit(node)

    # --- literals ---------------------------------------------------------

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str) and len(node.value) >= 16:
            for prefix, what in SECRET_HINTS:
                if node.value.startswith(prefix):
                    self.warn(
                        node,
                        "possible_secret",
                        f"this literal looks like {what}. Store it as a secret instead — source is stored and, for public functions, readable.",
                    )
                    break
        self.generic_visit(node)


def validate(source: str, entrypoint: str, max_bytes: int) -> dict:
    findings: list[Finding] = []
    raw = source.encode("utf-8")
    if len(raw) > max_bytes:
        findings.append(Finding("error", "too_large", f"source is {len(raw)} bytes; the limit is {max_bytes}"))
        return {"ok": False, "findings": findings, "functions": [], "imports": [], "capabilities": []}

    try:
        tree = ast.parse(source, filename="handler.py")
    except SyntaxError as exc:
        return {
            "ok": False,
            "findings": [Finding("error", "syntax_error", exc.msg or "syntax error", exc.lineno or 0, exc.offset or 0)],
            "functions": [],
            "imports": [],
            "capabilities": [],
        }

    v = Visitor()
    v.visit(tree)
    findings.extend(v.findings)

    if entrypoint not in v.functions:
        findings.append(
            Finding(
                "error",
                "missing_entrypoint",
                f"no function named `{entrypoint}` was found. Define `def {entrypoint}(request):` (an optional second `ctx` parameter is supported). Found: {', '.join(v.functions) or 'none'}",
            )
        )
    else:
        # Arity check against the runtime ABI.
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == entrypoint:
                pos = [a.arg for a in node.args.args]
                if len(pos) == 0:
                    findings.append(
                        Finding("error", "bad_signature", f"`{entrypoint}` must accept a request argument: def {entrypoint}(request):", node.lineno)
                    )
                elif len(pos) > 2:
                    findings.append(
                        Finding("error", "bad_signature", f"`{entrypoint}` takes at most (request, ctx); found {len(pos)} parameters", node.lineno)
                    )
                break

    errors = [f for f in findings if f["severity"] == "error"]
    return {
        "ok": not errors,
        "findings": findings,
        "functions": v.functions,
        "imports": sorted(v.imports),
        "capabilities": sorted(v.capabilities),
    }


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError as exc:
        print(json.dumps({"ok": False, "findings": [Finding("error", "bad_input", str(exc))]}))
        return 0
    report = validate(
        payload.get("source") or "",
        payload.get("entrypoint") or "main",
        int(payload.get("max_bytes") or 262144),
    )
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
