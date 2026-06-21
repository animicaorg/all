"""Context assembly for the Animica Studio Agent.

:class:`ContextBuilder` turns the current world-state (open project, selected /
relevant files, recent tool outputs, recent errors, chat history) into a compact
text block that fits within a size budget. It deliberately uses the indexer's
keyword search to surface *relevant* files instead of dumping the whole project,
and always redacts secrets via :func:`animica_studio.config.redact`.

The builder has no Qt dependency and imports headless.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from ..config import redact
from ..models import Message, MessageRole

if TYPE_CHECKING:  # pragma: no cover
    from ..db import Database
    from ..services.indexer import Indexer
    from ..services.project_service import ProjectService


# Rough chars-per-token heuristic for budgeting (we don't tokenize precisely).
_CHARS_PER_TOKEN = 4
# Default character budget for the assembled context block.
DEFAULT_MAX_CHARS = 12_000
# Per-file excerpt cap so a single big file can't eat the whole budget.
_MAX_FILE_EXCERPT_CHARS = 3_000
_MAX_RELEVANT_FILES = 6
_MAX_TOOL_OUTPUT_CHARS = 1_200
_MAX_ERROR_CHARS = 1_500
_MAX_TREE_NODES = 80


@dataclass
class ContextBuilder:
    """Assemble model context from project state, files, history and tool outputs."""

    project_service: Optional["ProjectService"] = None
    indexer: Optional["Indexer"] = None
    db: Optional["Database"] = None
    max_chars: int = DEFAULT_MAX_CHARS
    secrets: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------ #
    def build(
        self,
        *,
        user_request: str = "",
        selected_files: Optional[list[str]] = None,
        recent_tool_outputs: Optional[list[str]] = None,
        recent_errors: Optional[list[str]] = None,
        history: Optional[list[Message]] = None,
        project_id: Optional[str] = None,
    ) -> str:
        """Return a single context string within the configured character budget."""
        sections: list[str] = []
        budget = max(1_000, self.max_chars)

        # 1. Project summary (always cheap, high value).
        summary = self._project_summary()
        if summary:
            sections.append(self._section("Project overview", summary))

        # 2. File-tree summary (capped).
        tree = self._file_tree_summary()
        if tree:
            sections.append(self._section("File tree (top-level, truncated)", tree))

        # 3. Relevant / selected files.
        files_block = self._relevant_files(
            user_request=user_request,
            selected_files=selected_files or [],
            project_id=project_id,
        )
        if files_block:
            sections.append(self._section("Relevant files", files_block))

        # 4. Recent errors (so the agent can react to failures).
        if recent_errors:
            err = "\n\n".join(self._truncate(e, _MAX_ERROR_CHARS) for e in recent_errors if e)
            if err.strip():
                sections.append(self._section("Recent errors", err))

        # 5. Recent tool outputs.
        if recent_tool_outputs:
            tout = "\n\n".join(
                self._truncate(o, _MAX_TOOL_OUTPUT_CHARS) for o in recent_tool_outputs if o
            )
            if tout.strip():
                sections.append(self._section("Recent tool outputs", tout))

        # 6. Chat-history summary (older turns condensed).
        hist = self._history_summary(history or [])
        if hist:
            sections.append(self._section("Conversation so far (summary)", hist))

        # Join + enforce the overall budget (drop from the end if needed, but keep
        # the project overview which is first).
        assembled = self._fit_to_budget(sections, budget)
        return redact(assembled, *self.secrets)

    # ------------------------------------------------------------------ #
    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate (chars / 4)."""
        return max(1, len(text) // _CHARS_PER_TOKEN)

    # -- section builders ---------------------------------------------- #
    def _project_summary(self) -> str:
        ps = self.project_service
        if ps is None:
            return ""
        try:
            if hasattr(ps, "is_open") and not ps.is_open():
                return ""
            return self._truncate(ps.summarize_project(), 2_000)
        except Exception:
            return ""

    def _file_tree_summary(self) -> str:
        ps = self.project_service
        if ps is None:
            return ""
        try:
            if hasattr(ps, "is_open") and not ps.is_open():
                return ""
            tree = ps.file_tree("")
        except Exception:
            return ""
        lines: list[str] = []
        self._flatten_tree(tree, lines, depth=0)
        if len(lines) > _MAX_TREE_NODES:
            extra = len(lines) - _MAX_TREE_NODES
            lines = lines[:_MAX_TREE_NODES]
            lines.append(f"… (+{extra} more)")
        return "\n".join(lines)

    def _flatten_tree(self, nodes: list[dict[str, Any]], out: list[str], depth: int) -> None:
        if depth > 2:
            return
        for node in nodes:
            if len(out) >= _MAX_TREE_NODES + 1:
                return
            name = node.get("name", "?")
            is_dir = node.get("is_dir", False)
            out.append("  " * depth + name + ("/" if is_dir else ""))
            children = node.get("children")
            if children:
                self._flatten_tree(children, out, depth + 1)

    def _relevant_files(
        self,
        *,
        user_request: str,
        selected_files: list[str],
        project_id: Optional[str],
    ) -> str:
        ps = self.project_service
        if ps is None:
            return ""
        # Prefer explicitly selected files, then indexer-search hits.
        rel_paths: list[str] = []
        for p in selected_files:
            if p and p not in rel_paths:
                rel_paths.append(p)

        if self.indexer is not None and project_id and user_request.strip():
            try:
                hits = self.indexer.search(
                    project_id, user_request, limit=_MAX_RELEVANT_FILES * 2
                )
                for e in hits:
                    if e.path and e.path not in rel_paths:
                        rel_paths.append(e.path)
            except Exception:
                pass

        rel_paths = rel_paths[:_MAX_RELEVANT_FILES]
        blocks: list[str] = []
        for path in rel_paths:
            try:
                content = ps.read_file(path, max_bytes=_MAX_FILE_EXCERPT_CHARS * 2)
            except Exception:
                continue
            excerpt = self._truncate(content, _MAX_FILE_EXCERPT_CHARS)
            lang = _lang_from_path(path)
            blocks.append(f"### {path}\n```{lang}\n{excerpt}\n```")
        return "\n\n".join(blocks)

    def _history_summary(self, history: list[Message]) -> str:
        if not history:
            return ""
        # Keep the last few turns verbatim-ish; condense older ones to one line.
        keep_verbatim = 4
        lines: list[str] = []
        older = history[:-keep_verbatim] if len(history) > keep_verbatim else []
        recent = history[-keep_verbatim:] if len(history) > keep_verbatim else history
        for m in older:
            role = m.role.value if isinstance(m.role, MessageRole) else str(m.role)
            snippet = self._truncate(m.content.replace("\n", " "), 120)
            if snippet.strip():
                lines.append(f"- {role}: {snippet}")
        for m in recent:
            role = m.role.value if isinstance(m.role, MessageRole) else str(m.role)
            snippet = self._truncate(m.content, 400)
            if snippet.strip():
                lines.append(f"- {role}:\n{snippet}")
        return "\n".join(lines)

    # -- helpers -------------------------------------------------------- #
    @staticmethod
    def _section(title: str, body: str) -> str:
        return f"## {title}\n{body.strip()}"

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if text is None:
            return ""
        if len(text) <= limit:
            return text
        head = text[: limit - 40]
        return f"{head}\n… [truncated {len(text) - len(head)} chars]"

    def _fit_to_budget(self, sections: list[str], budget: int) -> str:
        out: list[str] = []
        used = 0
        for sec in sections:
            cost = len(sec) + 2
            if used + cost > budget and out:
                # Try to include a truncated version if it's the relevant-files or
                # history section; otherwise stop.
                remaining = budget - used
                if remaining > 400:
                    out.append(self._truncate(sec, remaining - 40))
                break
            out.append(sec)
            used += cost
        return "\n\n".join(out)


def _lang_from_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "tsx": "tsx",
        "jsx": "jsx",
        "rs": "rust",
        "go": "go",
        "java": "java",
        "rb": "ruby",
        "c": "c",
        "h": "c",
        "cpp": "cpp",
        "cc": "cpp",
        "cs": "csharp",
        "sh": "bash",
        "json": "json",
        "toml": "toml",
        "yaml": "yaml",
        "yml": "yaml",
        "md": "markdown",
        "html": "html",
        "css": "css",
        "sql": "sql",
    }.get(ext, "")


__all__ = ["ContextBuilder", "DEFAULT_MAX_CHARS"]
