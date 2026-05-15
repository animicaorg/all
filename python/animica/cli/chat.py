"""`animica chat` — registers the AICF-paid chat REPL.

This module is a thin re-export so the existing `animica` python CLI can
dispatch `animica chat` without duplicating REPL logic. The implementation
lives in agent_runtime/src/agent_runtime/cli/chat.py.

Importable from the source tree (which has ai/agent_runtime/src on its
path via __init__.py below) and from the published wheel (which bundles
agent_runtime as a top-level package).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import typer

# When running from the source tree, agent_runtime lives at
# <repo>/ai/agent_runtime/src/agent_runtime. The animica __init__.py adds
# the repo root to sys.path but not src dirs. We add it here lazily.
def _ensure_agent_runtime_on_path() -> None:
    try:
        import agent_runtime    # noqa: F401
        return
    except ImportError:
        pass
    # Try the source tree layout.
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "ai" / "agent_runtime" / "src"
        if (candidate / "agent_runtime" / "__init__.py").is_file():
            sys.path.insert(0, str(candidate))
            return
    # Last resort: the wheel-bundled location (force-included via pyproject).
    # No-op; the import below will fail with a clear error if absent.


_ensure_agent_runtime_on_path()

try:
    from agent_runtime.cli.chat import app as _chat_app
except Exception as exc:  # noqa: BLE001 — surface a helpful error
    app = typer.Typer(no_args_is_help=False, add_completion=False,
                      help="Open an interactive AICF-paid chat session.")

    @app.callback(invoke_without_command=True)
    def main(  # type: ignore[no-redef]
        _placeholder: Optional[str] = typer.Argument(None, hidden=True),
    ) -> None:
        typer.echo(
            "animica chat is unavailable in this environment.\n"
            f"agent_runtime import failed: {exc}\n"
            "Install the agent_runtime extras: "
            "`pip install agent_runtime` from <repo>/ai/agent_runtime, or "
            "install the animica wheel built with the [ai] extra.",
            err=True,
        )
        raise typer.Exit(code=2)
else:
    app = _chat_app
