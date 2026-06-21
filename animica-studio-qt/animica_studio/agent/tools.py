"""Tool registry + :class:`ToolManager` for the Animica Studio Agent.

Each tool is a thin, validated adapter over the services layer
(:mod:`animica_studio.services`). Every tool returns a uniform
:class:`animica_studio.models.ToolResult` (``ok`` / ``output`` / ``error`` / ``data``).

Risk model
----------
Tools are classified ``risky=True`` when they mutate files destructively, run
shell commands, install dependencies, or create git history. The *executor* is
responsible for gating risky calls behind the approval flow and autonomy level;
``ToolManager`` only declares the risk (:meth:`ToolManager.is_risky`) and executes.

Sandboxing
----------
All file-path arguments are funneled through the :class:`ProjectService`, whose
``resolve()`` rejects absolute paths, ``..`` escapes, and symlinks leaving the
project root. ``ToolManager`` additionally refuses file tools when no project is
open, so a stray call can never touch the host filesystem.

This module imports headless: the services are referenced via ``TYPE_CHECKING``
and duck typing, so importing it never requires Qt or the services to be built.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

from ..models import FileDiff, ToolResult
from .patching import PatchError, apply_patch

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids hard import at runtime
    from ..db import Database
    from ..services.command_runner import CommandRunner
    from ..services.git_service import GitService
    from ..services.indexer import Indexer
    from ..services.project_service import ProjectService
    from ..services.templates_service import TemplatesService


# --------------------------------------------------------------------------- #
# Tool registry metadata
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ToolSpec:
    """Static metadata for a single tool (name, risk, category, JSON schema)."""

    name: str
    description: str
    parameters: dict[str, Any]
    risky: bool = False
    category: Optional[str] = None  # approval category for risky tools

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


_STR = {"type": "string"}


# The stable tool catalog. Order is the order shown to the model.
TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "read_file",
        "Read the full text of a file (path relative to the project root).",
        _obj({"path": _STR}, ["path"]),
    ),
    ToolSpec(
        "write_file",
        "Create or overwrite a file with the given content (relative path).",
        _obj({"path": _STR, "content": _STR}, ["path", "content"]),
        risky=True,
    ),
    ToolSpec(
        "patch_file",
        "Apply a change to a file. Provide either a unified diff (`diff`) OR a "
        "search/replace pair (`search`/`replace`).",
        _obj(
            {
                "path": _STR,
                "diff": _STR,
                "search": _STR,
                "replace": _STR,
            },
            ["path"],
        ),
        risky=True,
    ),
    ToolSpec(
        "read_files",
        "Read several files at once (relative paths). Cheaper than many read_file "
        "calls — prefer this when you need 2+ files.",
        _obj({"paths": {"type": "array", "items": _STR}}, ["paths"]),
    ),
    ToolSpec(
        "list_files",
        "List files/directories under a relative path (tree, ignore-aware).",
        _obj({"path": {"type": "string", "default": ""}}, []),
    ),
    ToolSpec(
        "search_project",
        "Keyword-search the indexed project for files/symbols matching a query.",
        _obj({"query": _STR, "limit": {"type": "integer", "default": 20}}, ["query"]),
    ),
    ToolSpec(
        "create_file",
        "Create a new file with optional initial content (fails if it exists).",
        _obj({"path": _STR, "content": {"type": "string", "default": ""}}, ["path"]),
        risky=True,
    ),
    ToolSpec(
        "delete_file",
        "Delete a file (destructive — always requires approval).",
        _obj({"path": _STR}, ["path"]),
        risky=True,
        category="destructive",
    ),
    ToolSpec(
        "rename_file",
        "Rename or move a file from src to dst (relative paths).",
        _obj({"src": _STR, "dst": _STR}, ["src", "dst"]),
        risky=True,
    ),
    ToolSpec(
        "scaffold_project",
        "Lay down a known-good starter app from a built-in template into the open "
        "project (optionally under a subdirectory). Existing files are never "
        "overwritten. Use this to START a new app instead of writing every file by "
        "hand. Templates: python_cli, fastapi, qt_app, react, node_express, "
        "static_site, animica_agent, miner_dashboard, wallet_app.",
        _obj(
            {
                "template_id": _STR,
                "name": {"type": "string", "default": ""},
                "path": {"type": "string", "default": ""},
            },
            ["template_id"],
        ),
        risky=True,
    ),
    ToolSpec(
        "run_command",
        "Run a shell command in the project root. Risk is re-checked by the runner.",
        _obj({"command": _STR}, ["command"]),
        risky=True,
        category="general",
    ),
    ToolSpec(
        "run_tests",
        "Build/verify the project by running its test or compile command. With no "
        "'command' the studio auto-detects one (pytest / npm test / cargo build / "
        "go build / compileall). Use this to CONFIRM the app actually works after "
        "you change it.",
        _obj({"command": {"type": "string", "default": ""}}, []),
        risky=True,
        category="general",
    ),
    ToolSpec(
        "git_status",
        "Show the git status (porcelain) of the project.",
        _obj({}, []),
    ),
    ToolSpec(
        "git_diff",
        "Show the git diff, optionally for one file or for staged changes.",
        _obj(
            {"path": _STR, "staged": {"type": "boolean", "default": False}},
            [],
        ),
    ),
    ToolSpec(
        "git_commit",
        "Create a git commit (always requires approval).",
        _obj(
            {"message": _STR, "add_all": {"type": "boolean", "default": True}},
            ["message"],
        ),
        risky=True,
        category="deploy",
    ),
    ToolSpec(
        "install_dependency",
        "Install a package/dependency (always requires approval).",
        _obj({"command": _STR}, ["command"]),
        risky=True,
        category="install",
    ),
    ToolSpec(
        "detect_project_type",
        "Detect the project type (python/node/rust/go/...).",
        _obj({}, []),
    ),
    ToolSpec(
        "summarize_project",
        "Produce a human-readable overview of the project.",
        _obj({}, []),
    ),
)

TOOL_SPECS_BY_NAME: dict[str, ToolSpec] = {s.name: s for s in TOOL_SPECS}

# Tools whose write is "soft" — gated by autonomy level (diff-then-apply) rather
# than always requiring an explicit approval prompt.
SOFT_WRITE_TOOLS = frozenset(
    {"write_file", "patch_file", "create_file", "rename_file", "scaffold_project"}
)
# Tools that ALWAYS require an approval prompt regardless of autonomy level.
ALWAYS_APPROVE_TOOLS = frozenset(
    {"delete_file", "run_command", "git_commit", "install_dependency"}
)


# --------------------------------------------------------------------------- #
# Tool context
# --------------------------------------------------------------------------- #
@dataclass
class ToolContext:
    """The collaborators a tool needs to do its job.

    ``approve(action, category) -> bool`` is the synchronous approval gate the
    *executor* injects; ``ToolManager`` calls it for ALWAYS_APPROVE tools that the
    executor hasn't already cleared. For soft-write tools the executor handles the
    diff/approval flow itself, so ``approve`` is typically a pass-through there.
    """

    project_service: Optional["ProjectService"] = None
    git_service: Optional["GitService"] = None
    command_runner: Optional["CommandRunner"] = None
    indexer: Optional["Indexer"] = None
    templates_service: Optional["TemplatesService"] = None
    db: Optional["Database"] = None
    session_id: Optional[str] = None
    approve: Callable[[str, str], bool] = field(default=lambda action, category: True)


# --------------------------------------------------------------------------- #
# ToolManager
# --------------------------------------------------------------------------- #
class ToolManager:
    """Registers tools and dispatches calls to the services, returning ToolResults."""

    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx
        self._handlers: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
            "read_file": self._read_file,
            "read_files": self._read_files,
            "write_file": self._write_file,
            "patch_file": self._patch_file,
            "list_files": self._list_files,
            "search_project": self._search_project,
            "create_file": self._create_file,
            "delete_file": self._delete_file,
            "rename_file": self._rename_file,
            "scaffold_project": self._scaffold_project,
            "run_command": self._run_command,
            "run_tests": self._run_tests,
            "git_status": self._git_status,
            "git_diff": self._git_diff,
            "git_commit": self._git_commit,
            "install_dependency": self._install_dependency,
            "detect_project_type": self._detect_project_type,
            "summarize_project": self._summarize_project,
        }

    # -- introspection ------------------------------------------------------- #
    def list_tools(self) -> list[dict[str, Any]]:
        """OpenAI-style tool schemas for the model / catalog."""
        return [s.to_openai() for s in TOOL_SPECS]

    def spec(self, name: str) -> Optional[ToolSpec]:
        return TOOL_SPECS_BY_NAME.get(name)

    def is_risky(self, name: str, arguments: dict[str, Any]) -> bool:
        """Whether the named call is risky (also re-checks run_command content)."""
        spec = TOOL_SPECS_BY_NAME.get(name)
        if spec is None:
            return False
        if spec.risky:
            return True
        # run_command shouldn't reach here (it's spec.risky), but be defensive.
        if name == "run_command":
            cmd = str(arguments.get("command", ""))
            runner = self.ctx.command_runner
            if runner is not None:
                try:
                    return bool(runner.is_risky(cmd))
                except Exception:
                    return True
        return False

    def category_for(self, name: str, arguments: dict[str, Any]) -> str:
        """Approval category for a (risky) tool call."""
        spec = TOOL_SPECS_BY_NAME.get(name)
        if name in ("run_command", "install_dependency"):
            cmd = str(arguments.get("command", ""))
            runner = self.ctx.command_runner
            if runner is not None:
                try:
                    cat = runner.classify(cmd)
                    if cat:
                        return cat
                except Exception:
                    pass
            if name == "install_dependency":
                return "install"
            return "general"
        if spec and spec.category:
            return spec.category
        return "general"

    # -- dispatch ------------------------------------------------------------ #
    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool by name with validated arguments."""
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult.failure(f"Unknown tool: {name!r}")
        if not isinstance(arguments, dict):
            return ToolResult.failure(
                f"Tool {name!r} arguments must be an object, got {type(arguments).__name__}"
            )
        try:
            return handler(arguments)
        except FileNotFoundError as exc:
            return ToolResult.failure(f"Not found: {exc}")
        except PermissionError as exc:
            return ToolResult.failure(f"Permission/sandbox error: {exc}")
        except ValueError as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            return ToolResult.failure(f"{type(exc).__name__}: {exc}")

    # -- service guards ------------------------------------------------------ #
    def _require_project(self) -> "ProjectService":
        ps = self.ctx.project_service
        if ps is None:
            raise ValueError("No project service available")
        is_open = getattr(ps, "is_open", None)
        if callable(is_open) and not ps.is_open():
            raise ValueError("No project is open; open a project before file operations")
        return ps

    def _require_git(self) -> "GitService":
        gs = self.ctx.git_service
        if gs is None:
            raise ValueError("Git is not available for this project")
        return gs

    # -- file tools ---------------------------------------------------------- #
    def _read_file(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        path = self._arg_str(args, "path")
        content = ps.read_file(path)
        return ToolResult.success(content, path=path, bytes=len(content.encode("utf-8")))

    def _write_file(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        path = self._arg_str(args, "path")
        content = str(args.get("content", ""))
        old = ""
        is_new = True
        try:
            old = ps.read_file(path)
            is_new = False
        except Exception:
            is_new = True
        ps.write_file(path, content)
        self._reindex(path)
        diff = FileDiff(path=path, old_text=old, new_text=content, is_new=is_new)
        verb = "Created" if is_new else "Updated"
        return ToolResult.success(f"{verb} {path}", path=path, diff=diff.to_dict())

    def _patch_file(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        path = self._arg_str(args, "path")
        old = ps.read_file(path)
        diff_text = args.get("diff")
        search = args.get("search")
        replace = args.get("replace")
        try:
            new_text = apply_patch(
                old,
                diff=str(diff_text) if diff_text else None,
                search=None if search is None else str(search),
                replace=None if replace is None else str(replace),
            )
        except PatchError as exc:
            # Surface a precise, actionable error instead of silently corrupting
            # the file; the model can re-read and retry with correct anchors.
            return ToolResult.failure(f"patch_file: {exc}", path=path)
        if new_text == old:
            return ToolResult.success(f"No change needed for {path}", path=path)
        ps.write_file(path, new_text)
        self._reindex(path)
        diff = FileDiff(path=path, old_text=old, new_text=new_text)
        return ToolResult.success(f"Patched {path}", path=path, diff=diff.to_dict())

    def _read_files(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        paths = args.get("paths")
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not paths:
            raise ValueError("read_files requires a non-empty 'paths' array")
        blocks: list[str] = []
        read_ok: list[str] = []
        for raw in paths[:25]:
            p = str(raw)
            try:
                content = ps.read_file(p, max_bytes=40_000)
                blocks.append(f"### {p}\n{content}")
                read_ok.append(p)
            except Exception as exc:  # one bad path shouldn't sink the batch
                blocks.append(f"### {p}\n(could not read: {exc})")
        return ToolResult.success("\n\n".join(blocks), paths=read_ok, count=len(read_ok))

    def _list_files(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        rel = str(args.get("path", "") or "")
        tree = ps.file_tree(rel)
        return ToolResult.success(_render_tree(tree), path=rel, tree=tree)

    def _search_project(self, args: dict[str, Any]) -> ToolResult:
        query = self._arg_str(args, "query")
        limit = int(args.get("limit", 20) or 20)
        indexer = self.ctx.indexer
        ps = self.ctx.project_service
        project_id = self._project_id()
        if indexer is None or not project_id:
            raise ValueError("Project index is not available")
        entries = indexer.search(project_id, query, limit=limit)
        lines = [f"{e.path} — {e.summary or e.language}".strip() for e in entries]
        out = "\n".join(lines) if lines else f"No matches for {query!r}"
        return ToolResult.success(out, matches=[e.path for e in entries], count=len(entries))

    def _create_file(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        path = self._arg_str(args, "path")
        content = str(args.get("content", ""))
        ps.create_file(path, content)
        self._reindex(path)
        diff = FileDiff(path=path, old_text="", new_text=content, is_new=True)
        return ToolResult.success(f"Created {path}", path=path, diff=diff.to_dict())

    def _delete_file(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        path = self._arg_str(args, "path")
        if not self.ctx.approve(f"delete file {path}", "destructive"):
            return ToolResult.failure("Deletion denied by user", path=path)
        old = ""
        try:
            old = ps.read_file(path)
        except Exception:
            old = ""
        ps.delete_file(path)
        self._deindex(path)
        diff = FileDiff(path=path, old_text=old, new_text="", is_delete=True)
        return ToolResult.success(f"Deleted {path}", path=path, diff=diff.to_dict())

    def _rename_file(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        src = self._arg_str(args, "src")
        dst = self._arg_str(args, "dst")
        ps.rename_file(src, dst)
        self._deindex(src)
        self._reindex(dst)
        diff = FileDiff(path=src, is_rename=True, new_path=dst)
        return ToolResult.success(f"Renamed {src} -> {dst}", path=src, new_path=dst, diff=diff.to_dict())

    def _scaffold_project(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        ts = self.ctx.templates_service
        if ts is None:
            raise ValueError("Templates service is not available")
        template_id = self._arg_str(args, "template_id")
        render = getattr(ts, "render_files", None)
        if not callable(render):
            raise ValueError("Templates service does not support scaffolding")
        root_name = getattr(getattr(ps, "root", None), "name", "") or "app"
        name = str(args.get("name") or root_name)
        subdir = str(args.get("path", "") or "").strip().strip("/")
        try:
            files = render(template_id, name)
        except Exception as exc:
            return ToolResult.failure(f"scaffold_project: {exc}", template=template_id)
        if not files:
            return ToolResult.failure(f"Unknown or empty template: {template_id!r}")
        written: list[str] = []
        skipped: list[str] = []
        for rel, content in files.items():
            dest = f"{subdir}/{rel}" if subdir else rel
            try:
                if ps.exists(dest):
                    skipped.append(dest)
                    continue
                ps.write_file(dest, content)
                self._reindex(dest)
                written.append(dest)
            except Exception as exc:
                skipped.append(f"{dest} ({exc})")
        summary = [f"Scaffolded '{template_id}' ({len(written)} files):"]
        summary += [f"  + {p}" for p in written]
        if skipped:
            summary.append(f"Skipped {len(skipped)} existing/failed: {', '.join(skipped[:10])}")
        return ToolResult.success(
            "\n".join(summary), template=template_id, written=written, skipped=skipped
        )

    def _run_tests(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        runner = self.ctx.command_runner
        if runner is None:
            raise ValueError("Command runner is not available")
        cmd = str(args.get("command", "") or "").strip()
        if not cmd:
            suggest = getattr(ps, "suggest_verify_command", None)
            cmd = (suggest() if callable(suggest) else "") or ""
        if not cmd:
            return ToolResult.failure(
                "No verify/test command could be determined for this project; "
                "pass an explicit 'command'."
            )
        if not self.ctx.approve(cmd, "general"):
            return ToolResult.failure("Verify denied by user", command=cmd)
        code, output = runner.run_blocking(cmd, timeout=300.0)
        if code == 0:
            return ToolResult.success(
                output or "(verify passed)", command=cmd, exit_code=code, passed=True
            )
        return ToolResult.failure(
            f"Verify failed (exit {code})",
            output=output,
            command=cmd,
            exit_code=code,
            passed=False,
        )

    # -- command tools ------------------------------------------------------- #
    def _run_command(self, args: dict[str, Any]) -> ToolResult:
        cmd = self._arg_str(args, "command")
        runner = self.ctx.command_runner
        if runner is None:
            raise ValueError("Command runner is not available")
        category = self.category_for("run_command", args)
        if not self.ctx.approve(cmd, category):
            return ToolResult.failure("Command denied by user", command=cmd)
        code, output = runner.run_blocking(cmd)
        if code == 0:
            return ToolResult.success(output or "(no output)", command=cmd, exit_code=code)
        return ToolResult.failure(
            f"Command exited with code {code}", output=output, command=cmd, exit_code=code
        )

    def _install_dependency(self, args: dict[str, Any]) -> ToolResult:
        cmd = self._arg_str(args, "command")
        runner = self.ctx.command_runner
        if runner is None:
            raise ValueError("Command runner is not available")
        if not self.ctx.approve(cmd, "install"):
            return ToolResult.failure("Install denied by user", command=cmd)
        code, output = runner.run_blocking(cmd)
        if code == 0:
            return ToolResult.success(output or "(installed)", command=cmd, exit_code=code)
        return ToolResult.failure(
            f"Install exited with code {code}", output=output, command=cmd, exit_code=code
        )

    # -- git tools ----------------------------------------------------------- #
    def _git_status(self, args: dict[str, Any]) -> ToolResult:
        gs = self._require_git()
        if not gs.is_repo():
            return ToolResult.success("(not a git repository)")
        return ToolResult.success(gs.status() or "(clean)")

    def _git_diff(self, args: dict[str, Any]) -> ToolResult:
        gs = self._require_git()
        if not gs.is_repo():
            return ToolResult.success("(not a git repository)")
        path = args.get("path")
        staged = bool(args.get("staged", False))
        diff = gs.diff(str(path) if path else None, staged=staged)
        return ToolResult.success(diff or "(no diff)")

    def _git_commit(self, args: dict[str, Any]) -> ToolResult:
        gs = self._require_git()
        message = self._arg_str(args, "message")
        add_all = bool(args.get("add_all", True))
        if not self.ctx.approve(f"git commit: {message}", "deploy"):
            return ToolResult.failure("Commit denied by user")
        ok, out = gs.commit(message, add_all=add_all)
        if ok:
            return ToolResult.success(out)
        return ToolResult.failure("git commit failed", output=out)

    # -- project introspection ---------------------------------------------- #
    def _detect_project_type(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        return ToolResult.success(ps.detect_project_type())

    def _summarize_project(self, args: dict[str, Any]) -> ToolResult:
        ps = self._require_project()
        return ToolResult.success(ps.summarize_project())

    # -- helpers ------------------------------------------------------------- #
    @staticmethod
    def _arg_str(args: dict[str, Any], key: str) -> str:
        val = args.get(key)
        if val is None or not isinstance(val, str) or not val.strip():
            raise ValueError(f"Missing or invalid required argument: {key!r}")
        return val

    def _project_id(self) -> Optional[str]:
        ps = self.ctx.project_service
        if ps is None:
            return None
        # ProjectService doesn't expose project_id directly; derive from db.
        db = self.ctx.db
        root = getattr(ps, "root", None)
        if db is not None and root is not None:
            try:
                proj = db.get_project_by_path(str(root))
                if proj is not None:
                    return proj.id
            except Exception:
                pass
        return None

    # -- incremental index maintenance -------------------------------------- #
    def _reindex(self, path: str) -> None:
        """Re-index a freshly written file so search/context stays current."""
        indexer = self.ctx.indexer
        pid = self._project_id()
        if indexer is None or not pid:
            return
        try:
            indexer.index_file(pid, path)
        except Exception:  # indexing is best-effort, never fail the write
            pass

    def _deindex(self, path: str) -> None:
        """Drop a deleted/renamed file from the index."""
        db = self.ctx.db
        pid = self._project_id()
        if db is None or not pid:
            return
        try:
            db.delete_file_index(pid, path)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Backwards-compatible shim: the safe applier now lives in agent.patching.
# Kept so any external import of ``_apply_unified_diff`` keeps working.
# --------------------------------------------------------------------------- #
def _apply_unified_diff(original: str, diff_text: str) -> str:
    """Deprecated alias for :func:`animica_studio.agent.patching.apply_unified_diff`."""
    from .patching import apply_unified_diff

    return apply_unified_diff(original, diff_text)


def _render_tree(nodes: list[dict[str, Any]], indent: int = 0) -> str:
    """Render a file-tree node list (from ProjectService.file_tree) as text."""
    lines: list[str] = []
    pad = "  " * indent
    for node in nodes:
        name = node.get("name", "?")
        is_dir = node.get("is_dir", False)
        marker = "/" if is_dir else ""
        lines.append(f"{pad}{name}{marker}")
        children = node.get("children")
        if children:
            lines.append(_render_tree(children, indent + 1))
    return "\n".join(l for l in lines if l)


__all__ = [
    "ToolSpec",
    "TOOL_SPECS",
    "TOOL_SPECS_BY_NAME",
    "SOFT_WRITE_TOOLS",
    "ALWAYS_APPROVE_TOOLS",
    "ToolContext",
    "ToolManager",
]
