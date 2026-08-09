"""The developer-facing Animica Python Cloud SDK: ``App`` and ``@app.function``.

```python
from animica.cloud import App

app = App("my-api")

@app.function(memory_mb=256, timeout=30, capabilities=["AI_INFERENCE"])
def hello(request):
    return {"hello": request.get("name")}
```

The decorator is a THIN metadata layer over the real runtime ABI (sandbox/runner.py): the
platform executes a plain module-level ``def entrypoint(request)`` or ``(request, ctx)``
returning a JSON-serializable value, with the host API reached via ``import animica`` INSIDE
the sandbox — not via this SDK. This file therefore never invents sugar the runner can't
resolve: no async, no classes-as-handlers, no multi-file packages.

Why deployment must STRIP the SDK scaffolding: inside the sandbox, ``import animica`` yields
the capability-broker shim (runner.py builds it by hand); it has no ``cloud`` submodule, so
``from animica.cloud import App`` would ImportError at runtime. ``extract()`` produces a
sandbox-clean copy of the module — SDK imports, ``App(...)`` construction and ``@app.function``
decorator lines are blanked (line count preserved, so runtime tracebacks still point at the
developer's own line numbers) — and the platform resolves the entrypoint as the undecorated
``def``. The runner ALSO unwraps ``__animica_function__`` markers defensively; we set the
marker anyway so even unstripped source degrades gracefully wherever the SDK is importable.

Relationship to ``animica.studio``: same family, different substrate. Studio dispatches
arbitrary pickled callables to the AICF fleet; Cloud deploys *source* to the animica.dev
Python Cloud HTTP platform where it is anchored on-chain (DEPLOY tx) and executed off-chain
in the hardened sandbox. Keep the DX parallel, never merge the mechanics.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import (
    CAPABILITIES,
    DEFAULT_MEMORY_MB,
    DEFAULT_TIMEOUT_MS,
    MAX_MEMORY_MB,
    MAX_TIMEOUT_MS,
    MIN_MEMORY_MB,
    MIN_TIMEOUT_MS,
)
from .errors import ExtractionError, NotDeployedError

_SDK_MODULE = "animica.cloud"


def _slugify(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _validate_slug(slug: str, what: str) -> str:
    ok = (
        1 <= len(slug) <= 64
        and slug[0].isalpha()
        and all(c.isalnum() or c == "-" for c in slug)
        and not slug.endswith("-")
    )
    if not ok:
        raise ValueError(
            f"invalid {what} {slug!r}: must be 1-64 chars, start with a letter, "
            f"contain only [a-z0-9-], not end with '-'"
        )
    return slug


class Function:
    """A function registered with an :class:`App`.

    Stays a normal callable — ``hello({"name": "x"})`` runs your code in THIS process with no
    sandbox and no host API (a 2-parameter handler receives ``ctx=None`` locally; the real
    ``ctx`` only exists inside the platform sandbox). ``.remote()`` invokes the DEPLOYED
    version through the Python Cloud API and costs real ANM per the function's metering.
    """

    #: Marker the sandbox runner checks (runner.py _load_entrypoint) to unwrap a decorated
    #: object back to the raw callable. Keep name + `.fn` attribute in sync with runner.py.
    __animica_function__ = True

    def __init__(
        self,
        app: "App",
        fn: Callable,
        *,
        name: Optional[str],
        memory_mb: int,
        timeout: "int | float",
        capabilities: List[str],
        description: str,
        per_call_nanm: int,
        requires_auth: bool,
    ) -> None:
        if not callable(fn):
            raise TypeError("@app.function must decorate a callable")
        if fn.__name__ == "<lambda>":
            raise ValueError("lambdas cannot be deployed — the entrypoint must be a named def")
        # The platform resolves the entrypoint as getattr(module, name): only module-level
        # defs qualify. Nested/class-scoped functions have a dotted __qualname__.
        if fn.__qualname__ != fn.__name__:
            raise ValueError(
                f"{fn.__qualname__}: @app.function only works on MODULE-LEVEL functions — "
                f"the runtime resolves the entrypoint by name on the module"
            )
        self._validate_signature(fn)

        self.app = app
        self.fn = fn
        self.entrypoint = fn.__name__
        self.name = _validate_slug(name or _slugify(fn.__name__), "function name")
        self.description = description or (inspect.getdoc(fn) or "").split("\n", 1)[0][:200]

        timeout_ms = int(round(float(timeout) * 1000))
        if not (MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS):
            raise ValueError(
                f"{fn.__name__}: timeout must be {MIN_TIMEOUT_MS // 1000}..{MAX_TIMEOUT_MS // 1000} "
                f"seconds, got {timeout!r}"
            )
        self.timeout_ms = timeout_ms

        memory_mb = int(memory_mb)
        if not (MIN_MEMORY_MB <= memory_mb <= MAX_MEMORY_MB):
            raise ValueError(
                f"{fn.__name__}: memory_mb must be {MIN_MEMORY_MB}..{MAX_MEMORY_MB}, got {memory_mb}"
            )
        self.memory_mb = memory_mb

        caps = [str(c).upper() for c in capabilities]
        unknown = [c for c in caps if c not in CAPABILITIES]
        if unknown:
            raise ValueError(
                f"{fn.__name__}: unknown capabilities {unknown}; valid: {', '.join(CAPABILITIES)}"
            )
        self.capabilities = caps

        self.per_call_nanm = int(per_call_nanm)
        if self.per_call_nanm < 0:
            raise ValueError(f"{fn.__name__}: per_call_nanm must be >= 0")
        self.requires_auth = bool(requires_auth)

        # Carry identity/docs like functools.wraps without clobbering our own attributes.
        self.__name__ = fn.__name__
        self.__doc__ = fn.__doc__
        self.__wrapped__ = fn
        self.__module__ = fn.__module__

    @staticmethod
    def _validate_signature(fn: Callable) -> None:
        """Enforce the EXACT runner ABI: 1-2 positional parameters, nothing keyword-only.

        The runner's arity probe counts every non-var parameter (including keyword-only), and
        calls ``fn(request, ctx)`` positionally whenever it counts >= 2 — so a keyword-only
        parameter would silently receive the ctx object or blow up at invocation time. Refuse
        at decoration time with a message that says what the ABI actually is.
        """
        sig = inspect.signature(fn)
        positional = [
            p
            for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        kw_only = [p for p in sig.parameters.values() if p.kind == p.KEYWORD_ONLY]
        if kw_only:
            raise ValueError(
                f"{fn.__name__}: keyword-only parameters ({', '.join(p.name for p in kw_only)}) "
                f"are not supported by the runtime ABI — use def {fn.__name__}(request) or "
                f"def {fn.__name__}(request, ctx)"
            )
        if not 1 <= len(positional) <= 2:
            raise ValueError(
                f"{fn.__name__}: the runtime calls def {fn.__name__}(request) or "
                f"def {fn.__name__}(request, ctx) — found {len(positional)} positional parameters"
            )

    # ---- local + remote execution ---------------------------------------

    def __call__(self, request: Any = None, ctx: Any = None) -> Any:
        """Run locally, in-process. No sandbox, no metering, no host API."""
        params = [
            p
            for p in inspect.signature(self.fn).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        if len(params) >= 2:
            return self.fn(request, ctx)
        return self.fn(request)

    def remote(
        self,
        request: Any = None,
        *,
        client: Any = None,
        max_spend_nanm: Optional[int] = None,
    ) -> dict:
        """Invoke the DEPLOYED function via the Python Cloud API (metered, billed in ANM).

        Returns the full invoke response ({requestId, status, result, receipt, ...}); raises
        :class:`NotDeployedError` when no function with this slug exists under your account.
        """
        from .client import CloudClient

        c = client or CloudClient()
        fn = c.find_function(self.name)
        if fn is None:
            raise NotDeployedError(
                f"function {self.name!r} is not deployed for this account — "
                f"run `animica cloud deploy` first"
            )
        return c.invoke(fn["id"], request, max_spend_nanm=max_spend_nanm)

    # ---- platform mapping -------------------------------------------------

    def platform_config(self) -> dict:
        """The CloudFunction fields this decoration maps to, keyed as the API expects."""
        return {
            "slug": self.name,
            "name": self.name,
            "entrypoint": self.entrypoint,
            "timeoutMs": self.timeout_ms,
            "memoryMb": self.memory_mb,
            "capabilities": list(self.capabilities),
            "description": self.description,
            "perCallNanm": str(self.per_call_nanm),
            "requiresAuth": self.requires_auth,
        }

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        caps = f" caps={self.capabilities}" if self.capabilities else ""
        return (
            f"<cloud.Function {self.app.name}/{self.name} entrypoint={self.entrypoint} "
            f"{self.memory_mb}MB {self.timeout_ms}ms{caps}>"
        )


class App:
    """A named group of cloud functions. Purely a deploy-time organizer: the App object never
    exists inside the sandbox (extraction strips it), so keep all runtime behavior in the
    functions themselves."""

    def __init__(self, name: str) -> None:
        self.name = _validate_slug(_slugify(name), "app name")
        self.functions: Dict[str, Function] = {}

    def function(
        self,
        _fn: Optional[Callable] = None,
        *,
        name: Optional[str] = None,
        memory_mb: int = DEFAULT_MEMORY_MB,
        timeout: "int | float" = DEFAULT_TIMEOUT_MS / 1000,
        capabilities: Optional[List[str]] = None,
        description: str = "",
        per_call_nanm: int = 0,
        requires_auth: bool = False,
    ):
        """Register a function for deployment.

        ``timeout`` is SECONDS (matching the studio SDK); the platform stores milliseconds.
        ``capabilities`` must name real platform capabilities — they are declared here,
        granted at deploy time and enforced per-call by the sandbox host broker; importing
        nothing changes what a function may do.
        ``per_call_nanm`` is the developer surcharge in integer nANM added on top of metered
        cost (0 = pure cost-plus metering).
        """

        def deco(fn: Callable) -> Function:
            f = Function(
                self,
                fn,
                name=name,
                memory_mb=memory_mb,
                timeout=timeout,
                capabilities=list(capabilities or []),
                description=description,
                per_call_nanm=per_call_nanm,
                requires_auth=requires_auth,
            )
            if f.name in self.functions:
                raise ValueError(f"duplicate function name {f.name!r} in app {self.name!r}")
            self.functions[f.name] = f
            return f

        return deco(_fn) if callable(_fn) else deco

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<cloud.App {self.name!r} functions={list(self.functions)}>"


# ---------------------------------------------------------------------------
# Extraction: developer file -> deployable {source, entrypoint, config}
# ---------------------------------------------------------------------------


@dataclass
class ExtractedFunction:
    """One deployable unit: platform config + which entrypoint in the shared source."""

    app_name: str
    config: dict  # Function.platform_config() shape
    entrypoint: str


@dataclass
class Extraction:
    """Result of :func:`extract`. ``source`` is the sandbox-clean module all functions in the
    file share; each function deploys that source with its own entrypoint + config."""

    source: str
    source_sha256: str
    functions: List[ExtractedFunction] = field(default_factory=list)
    #: True when the file used no SDK scaffolding (plain `def main(request):` handler).
    bare: bool = False


def _mark_lines(blank: set, node: ast.AST) -> None:
    for ln in range(node.lineno, (getattr(node, "end_lineno", None) or node.lineno) + 1):
        blank.add(ln)


def strip_sdk_source(source: str) -> str:
    """Return a sandbox-clean copy of ``source`` with identical line numbering.

    Removed (line-blanked, never reflowed — tracebacks must keep pointing at the developer's
    real lines):
      * imports of animica.cloud in any spelling (``from animica.cloud import App``,
        ``import animica.cloud [as x]``, ``from animica import cloud``) — the sandbox's
        ``animica`` shim has no ``cloud`` and the import would fail before the entrypoint runs;
      * module-level ``x = App(...)`` constructions rooted at those imports;
      * ``@x.function(...)`` decorator lines on module-level defs.

    ``import animica.cloud`` (no alias) binds the name ``animica`` — that line is REPLACED
    with ``import animica`` so host-API references (animica.ai, animica.wallet, ...) keep
    working against the sandbox shim instead of dying with NameError.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ExtractionError(f"source does not parse: {exc}") from exc

    blank: set = set()
    replace: Dict[int, str] = {}  # lineno -> replacement text
    sdk_roots: set = set()  # names whose attributes may reach App (e.g. "animica", "ac", "cloud")
    app_ctor_names: set = set()  # direct names bound to the App class (e.g. "App", "CloudApp")

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _SDK_MODULE or alias.name.startswith(_SDK_MODULE + "."):
                    _mark_lines(blank, node)
                    if alias.asname:
                        sdk_roots.add(alias.asname)
                    else:
                        # `import animica.cloud` binds "animica"; keep the shim import alive.
                        sdk_roots.add("animica")
                        replace[node.lineno] = "import animica"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == _SDK_MODULE or mod.startswith(_SDK_MODULE + "."):
                _mark_lines(blank, node)
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if alias.name == "App":
                        app_ctor_names.add(bound)
                    else:
                        sdk_roots.add(bound)
            elif mod == "animica":
                for alias in node.names:
                    if alias.name == "cloud":
                        _mark_lines(blank, node)
                        sdk_roots.add(alias.asname or "cloud")

    def _is_app_ctor(call: ast.AST) -> bool:
        if not isinstance(call, ast.Call):
            return False
        f = call.func
        if isinstance(f, ast.Name):
            return f.id in app_ctor_names
        # animica.cloud.App(...) / ac.App(...) / cloud.App(...)
        if isinstance(f, ast.Attribute) and f.attr == "App":
            root = f.value
            while isinstance(root, ast.Attribute):
                root = root.value
            return isinstance(root, ast.Name) and root.id in sdk_roots
        return False

    app_vars: set = set()
    for node in tree.body:
        value = getattr(node, "value", None)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and value is not None and _is_app_ctor(value):
            _mark_lines(blank, node)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    app_vars.add(t.id)

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == "function"
                    and isinstance(target.value, ast.Name)
                    and target.value.id in app_vars
                ):
                    _mark_lines(blank, dec)
                    # The '@' sits on the decorator's first line; ast anchors past it.
                    blank.add(dec.lineno)

    lines = source.splitlines()
    out = []
    for i, line in enumerate(lines, start=1):
        if i in replace:
            out.append(replace[i])
        elif i in blank:
            out.append("")
        else:
            out.append(line)
    stripped = "\n".join(out)
    if source.endswith("\n"):
        stripped += "\n"

    try:
        ast.parse(stripped)
    except SyntaxError as exc:  # e.g. `app = App("x"); something_else()` on one line
        raise ExtractionError(
            f"could not strip SDK scaffolding cleanly (line {exc.lineno}): keep imports, "
            f"App() construction and decorators on their own dedicated lines"
        ) from exc
    return stripped


def _import_user_module(path: Path):
    """Import the developer's file so decorators run and register metadata. This EXECUTES
    their module top-level — exactly what any deploy tool that reads decorator config must do
    (and what the sandbox will do at invoke time anyway)."""
    digest = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    mod_name = f"_anm_cloud_user_{digest}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ExtractionError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered so dataclasses/typing introspection inside the user module resolves.
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except ExtractionError:
        raise
    except BaseException as exc:  # noqa: BLE001 - surface THEIR error with OUR context
        sys.modules.pop(mod_name, None)
        raise ExtractionError(f"importing {path.name} failed: {exc.__class__.__name__}: {exc}") from exc
    return module


def extract(path: "str | Path") -> Extraction:
    """Turn a developer file into deployable material.

    SDK files (containing ``App`` instances) yield one :class:`ExtractedFunction` per
    ``@app.function``; the shared ``source`` is the stripped, sandbox-clean module. Files with
    no SDK usage come back ``bare=True`` with no function list — the caller picks the
    entrypoint (default ``main``) and deploys the source as-is.
    """
    p = Path(path)
    try:
        source = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtractionError(f"cannot read {p}: {exc}") from exc

    uses_sdk = _SDK_MODULE in source or "from animica import cloud" in source
    if not uses_sdk:
        # Plain handler: nothing to strip, nothing to import (never execute code we don't
        # have to). Validation of entrypoint/signature is the validator's job.
        sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return Extraction(source=source, source_sha256=sha, functions=[], bare=True)

    stripped = strip_sdk_source(source)
    module = _import_user_module(p)

    apps: List[App] = []
    seen: set = set()
    for value in vars(module).values():
        if isinstance(value, App) and id(value) not in seen:
            seen.add(id(value))
            apps.append(value)

    functions: List[ExtractedFunction] = []
    # Entrypoints must exist as module-level defs in the STRIPPED source, or the platform
    # could accept a deploy it can never execute. Verify against the artifact we ship.
    stripped_defs = {
        n.name for n in ast.parse(stripped).body if isinstance(n, ast.FunctionDef)
    }
    slugs: set = set()
    for a in apps:
        for f in a.functions.values():
            if f.entrypoint not in stripped_defs:
                raise ExtractionError(
                    f"entrypoint {f.entrypoint!r} is not a module-level def in the deployable "
                    f"source — @app.function must decorate top-level functions in this file"
                )
            if f.name in slugs:
                raise ExtractionError(f"duplicate function slug {f.name!r} across apps in {p.name}")
            slugs.add(f.name)
            functions.append(
                ExtractedFunction(app_name=a.name, config=f.platform_config(), entrypoint=f.entrypoint)
            )

    if not functions:
        raise ExtractionError(
            f"{p.name} imports animica.cloud but registers no functions — decorate at least "
            f"one module-level def with @app.function"
        )

    sha = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
    return Extraction(source=stripped, source_sha256=sha, functions=functions, bare=False)
