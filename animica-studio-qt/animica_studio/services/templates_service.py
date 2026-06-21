"""Project template service for Animica Studio Qt.

:class:`TemplatesService` enumerates starter templates and materializes them into
a destination directory. Template *file contents* live on disk under
``animica_studio/templates/<template_id>/`` (written by the assets agent). This
service only enumerates and copies them, applying lightweight ``{{name}}`` /
``{{slug}}`` substitution to text files. When a template's source directory is
missing, a minimal built-in fallback is generated so the wizard always works.

The diff-viewer / project-wizard interface (per CONTRACTS) is::

    list_templates() -> list[dict]
    create(template_id, dest_dir, name) -> str  # returns project root path

with :meth:`list` / :meth:`materialize` provided as friendly aliases.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Ordered catalog of templates. ``id`` matches a directory under
# ``animica_studio/templates/<id>/`` when present.
_TEMPLATES: tuple[dict[str, str], ...] = (
    {
        "id": "python_cli",
        "name": "Python CLI",
        "description": "A minimal argparse-based command-line application.",
        "project_type": "python",
    },
    {
        "id": "fastapi",
        "name": "FastAPI Service",
        "description": "A FastAPI app with a health route and uvicorn entrypoint.",
        "project_type": "fastapi",
    },
    {
        "id": "qt_app",
        "name": "PySide6 Qt App",
        "description": "A minimal PySide6 desktop window.",
        "project_type": "qt",
    },
    {
        "id": "react",
        "name": "React App",
        "description": "A minimal React + Vite single-page app.",
        "project_type": "react",
    },
    {
        "id": "node_express",
        "name": "Node Express API",
        "description": "An Express.js HTTP API server.",
        "project_type": "node",
    },
    {
        "id": "static_site",
        "name": "Static Site",
        "description": "A plain HTML/CSS/JS static website.",
        "project_type": "static",
    },
    {
        "id": "animica_agent",
        "name": "Animica Agent",
        "description": "An Animica inference agent using the OpenAI-compatible API.",
        "project_type": "animica",
    },
    {
        "id": "miner_dashboard",
        "name": "Miner Dashboard",
        "description": "A static HTML/CSS/JS dashboard for Animica pool mining stats.",
        "project_type": "static",
    },
    {
        "id": "wallet_app",
        "name": "Wallet App",
        "description": "A minimal Animica wallet UI skeleton (read-only).",
        "project_type": "python",
    },
)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "app"


def _safe_name(name: str) -> str:
    """A display name safe to substitute into source string literals.

    Strips characters (quotes, braces, backticks, backslashes) that would break
    a Python f-string, a JS template literal, or a JSON value when the token is
    injected verbatim into a template file.
    """
    cleaned = re.sub(r'["\'`{}\\]', "", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "App"


# Extensions treated as text for {{name}}/{{slug}} substitution.
_TEXT_EXT = {
    ".py", ".txt", ".md", ".rst", ".json", ".toml", ".cfg", ".ini",
    ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css", ".scss",
    ".yaml", ".yml", ".sh", ".env", ".gitignore", ".qss", ".xml", "",
}

# Files/dirs never copied from a template source.
_TEMPLATE_IGNORE = {".git", "__pycache__", "node_modules", ".venv", ".DS_Store"}


class TemplatesService:
    """Enumerate and materialize starter project templates."""

    def __init__(self, templates_dir: Optional[str | Path] = None) -> None:
        if templates_dir is not None:
            self._templates_dir = Path(templates_dir)
        else:
            # Package directory: animica_studio/templates
            self._templates_dir = Path(__file__).resolve().parent.parent / "templates"

    @property
    def templates_dir(self) -> Path:
        return self._templates_dir

    # ------------------------------------------------------------------ #
    # Enumeration
    # ------------------------------------------------------------------ #
    def list_templates(self) -> list[dict[str, Any]]:
        """Return the catalog of available templates as dicts."""
        out: list[dict[str, Any]] = []
        for tpl in _TEMPLATES:
            entry = dict(tpl)
            src = self._templates_dir / tpl["id"]
            entry["available"] = src.is_dir()
            out.append(entry)
        return out

    # Friendly alias.
    def list(self) -> list[dict[str, Any]]:
        return self.list_templates()

    def get_template(self, template_id: str) -> Optional[dict[str, Any]]:
        for tpl in self.list_templates():
            if tpl["id"] == template_id:
                return tpl
        return None

    # ------------------------------------------------------------------ #
    # Materialization
    # ------------------------------------------------------------------ #
    def create(self, template_id: str, dest_dir: str, name: str) -> str:
        """Materialize ``template_id`` into ``dest_dir/<slug>`` and return root.

        ``dest_dir`` is the *parent* directory; the new project is created in a
        subdirectory named after the project slug.
        """
        meta = self.get_template(template_id)
        if meta is None:
            raise ValueError(f"Unknown template: {template_id}")

        slug = _slugify(name)
        parent = Path(dest_dir).expanduser()
        parent.mkdir(parents=True, exist_ok=True)
        root = (parent / slug).resolve()
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f"Destination is not empty: {root}")
        root.mkdir(parents=True, exist_ok=True)

        src = self._templates_dir / template_id
        ctx = {"name": _safe_name(name) or slug, "slug": slug}
        if src.is_dir():
            self._copy_tree(src, root, ctx)
        else:
            logger.info("Template source missing for %s; using fallback.", template_id)
            self._write_fallback(template_id, root, ctx)
        return str(root)

    # Friendly alias matching the task wording.
    def materialize(self, template_id: str, dest_dir: str, name: str) -> str:
        return self.create(template_id, dest_dir, name)

    def render_files(self, template_id: str, name: str) -> dict[str, str]:
        """Return ``{relative_path: text_content}`` for ``template_id`` (no disk I/O).

        Used by the agent's ``scaffold_project`` tool so it can lay starter files
        down through the sandboxed :class:`ProjectService` (rather than writing to
        an arbitrary directory). Binary template files are skipped. Falls back to
        the built-in minimal app when the on-disk template directory is absent.
        """
        meta = self.get_template(template_id)
        if meta is None:
            raise ValueError(f"Unknown template: {template_id}")
        slug = _slugify(name)
        ctx = {"name": _safe_name(name) or slug, "slug": slug}
        src = self._templates_dir / template_id
        if src.is_dir():
            out: dict[str, str] = {}
            self._collect_tree(src, src, ctx, out)
            if out:
                return out
        return dict(self._fallback_files(template_id, ctx))

    def _collect_tree(
        self, base: Path, current: Path, ctx: dict[str, str], out: dict[str, str]
    ) -> None:
        for item in sorted(current.iterdir(), key=lambda p: p.name):
            if item.name in _TEMPLATE_IGNORE:
                continue
            if item.is_dir():
                self._collect_tree(base, item, ctx, out)
                continue
            rel = item.relative_to(base).as_posix()
            if item.suffix.lower() in _TEXT_EXT:
                try:
                    out[rel] = self._substitute(item.read_text(encoding="utf-8"), ctx)
                    continue
                except (UnicodeDecodeError, OSError):
                    pass
            # Skip binary / unreadable template files for the agent path.

    # ------------------------------------------------------------------ #
    # Copy + substitution
    # ------------------------------------------------------------------ #
    def _copy_tree(self, src: Path, dest: Path, ctx: dict[str, str]) -> None:
        for item in src.iterdir():
            if item.name in _TEMPLATE_IGNORE:
                continue
            target = dest / item.name
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                self._copy_tree(item, target, ctx)
            else:
                self._copy_file(item, target, ctx)

    def _copy_file(self, src: Path, dest: Path, ctx: dict[str, str]) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix.lower() in _TEXT_EXT:
            try:
                text = src.read_text(encoding="utf-8")
                dest.write_text(self._substitute(text, ctx), encoding="utf-8")
                return
            except (UnicodeDecodeError, OSError):
                pass
        shutil.copy2(src, dest)

    @staticmethod
    def _substitute(text: str, ctx: dict[str, str]) -> str:
        out = text
        for key, value in ctx.items():
            out = out.replace("{{" + key + "}}", value)
        return out

    # ------------------------------------------------------------------ #
    # Built-in fallbacks (used only when a template dir is absent)
    # ------------------------------------------------------------------ #
    def _write_fallback(self, template_id: str, root: Path, ctx: dict[str, str]) -> None:
        files = self._fallback_files(template_id, ctx)
        for rel, content in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def _fallback_files(self, template_id: str, ctx: dict[str, str]) -> dict[str, str]:
        name = ctx["name"]
        slug = ctx["slug"]
        readme = f"# {name}\n\nGenerated by Animica Studio Qt ({template_id} template).\n"
        gitignore = "__pycache__/\n*.pyc\n.venv/\nnode_modules/\ndist/\nbuild/\n.env\n"

        if template_id == "python_cli":
            return {
                "README.md": readme,
                ".gitignore": gitignore,
                "requirements.txt": "",
                "main.py": (
                    "import argparse\n\n\n"
                    "def main() -> int:\n"
                    f'    parser = argparse.ArgumentParser(description="{name}")\n'
                    '    parser.add_argument("--name", default="world")\n'
                    "    args = parser.parse_args()\n"
                    '    print(f"Hello, {args.name}!")\n'
                    "    return 0\n\n\n"
                    'if __name__ == "__main__":\n'
                    "    raise SystemExit(main())\n"
                ),
            }
        if template_id == "fastapi":
            title = name
            return {
                "README.md": readme,
                ".gitignore": gitignore,
                "requirements.txt": "fastapi\nuvicorn\n",
                "main.py": (
                    "from fastapi import FastAPI\n\n"
                    f'app = FastAPI(title="{title}")\n\n\n'
                    '@app.get("/health")\n'
                    "def health() -> dict:\n"
                    '    return {"status": "ok"}\n\n\n'
                    '@app.get("/")\n'
                    "def index() -> dict:\n"
                    f'    return {{"app": "{title}"}}\n\n\n'
                    'if __name__ == "__main__":\n'
                    "    import uvicorn\n"
                    '    uvicorn.run(app, host="127.0.0.1", port=8000)\n'
                ),
            }
        if template_id in ("qt_app", "wallet_app"):
            return {
                "README.md": readme,
                ".gitignore": gitignore,
                "requirements.txt": "PySide6\n",
                "main.py": (
                    "from PySide6.QtWidgets import QApplication, QLabel\n\n\n"
                    "def main() -> int:\n"
                    "    app = QApplication([])\n"
                    f'    label = QLabel("{name}")\n'
                    "    label.resize(360, 120)\n"
                    "    label.show()\n"
                    "    return app.exec()\n\n\n"
                    'if __name__ == "__main__":\n'
                    "    raise SystemExit(main())\n"
                ),
            }
        if template_id == "react":
            return {
                "README.md": readme,
                ".gitignore": gitignore,
                "package.json": (
                    "{\n"
                    f'  "name": "{slug}",\n'
                    '  "version": "0.1.0",\n'
                    '  "private": true,\n'
                    '  "scripts": {"dev": "vite", "build": "vite build"},\n'
                    '  "dependencies": {"react": "^18.2.0", "react-dom": "^18.2.0"},\n'
                    '  "devDependencies": {"vite": "^5.0.0"}\n'
                    "}\n"
                ),
                "index.html": (
                    "<!doctype html>\n<html>\n<head>\n"
                    f"  <title>{name}</title>\n</head>\n<body>\n"
                    '  <div id="root"></div>\n'
                    '  <script type="module" src="/src/main.jsx"></script>\n'
                    "</body>\n</html>\n"
                ),
                "src/main.jsx": (
                    "import React from 'react'\n"
                    "import { createRoot } from 'react-dom/client'\n\n"
                    f"function App() {{ return <h1>{name}</h1> }}\n\n"
                    "createRoot(document.getElementById('root')).render(<App />)\n"
                ),
            }
        if template_id == "node_express":
            return {
                "README.md": readme,
                ".gitignore": gitignore,
                "package.json": (
                    "{\n"
                    f'  "name": "{slug}",\n'
                    '  "version": "0.1.0",\n'
                    '  "main": "index.js",\n'
                    '  "scripts": {"start": "node index.js"},\n'
                    '  "dependencies": {"express": "^4.18.2"}\n'
                    "}\n"
                ),
                "index.js": (
                    "const express = require('express')\n"
                    "const app = express()\n"
                    "app.get('/health', (req, res) => res.json({ status: 'ok' }))\n"
                    f"app.get('/', (req, res) => res.json({{ app: '{name}' }}))\n"
                    "const port = process.env.PORT || 3000\n"
                    "app.listen(port, () => console.log(`listening on ${port}`))\n"
                ),
            }
        if template_id in ("static_site", "miner_dashboard"):
            heading = "Miner Dashboard" if template_id == "miner_dashboard" else name
            return {
                "README.md": readme,
                "index.html": (
                    "<!doctype html>\n<html lang=\"en\">\n<head>\n"
                    '  <meta charset="utf-8" />\n'
                    '  <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
                    f"  <title>{name}</title>\n"
                    '  <link rel="stylesheet" href="style.css" />\n'
                    "</head>\n<body>\n"
                    f"  <h1>{heading}</h1>\n"
                    '  <script src="app.js"></script>\n'
                    "</body>\n</html>\n"
                ),
                "style.css": "body { font-family: sans-serif; margin: 2rem; }\n",
                "app.js": "console.log('hello');\n",
            }
        if template_id == "animica_agent":
            return {
                "README.md": readme,
                ".gitignore": gitignore,
                "requirements.txt": "httpx\n",
                "agent.py": (
                    "import os\n"
                    "import httpx\n\n"
                    'ENDPOINT = os.environ.get("ANIMICA_ENDPOINT", '
                    '"https://pool.animica.org/v1")\n'
                    'API_KEY = os.environ.get("ANIMICA_API_KEY", "")\n'
                    'MODEL = os.environ.get("ANIMICA_MODEL", "anm-fast-8b")\n\n\n'
                    "def ask(prompt: str) -> str:\n"
                    "    headers = {}\n"
                    "    if API_KEY:\n"
                    '        headers["Authorization"] = f"Bearer {API_KEY}"\n'
                    "    payload = {\n"
                    '        "model": MODEL,\n'
                    '        "messages": [{"role": "user", "content": prompt}],\n'
                    "    }\n"
                    "    resp = httpx.post(\n"
                    '        f"{ENDPOINT.rstrip(chr(47))}/chat/completions",\n'
                    "        json=payload, headers=headers, timeout=60.0,\n"
                    "    )\n"
                    "    resp.raise_for_status()\n"
                    '    return resp.json()["choices"][0]["message"]["content"]\n\n\n'
                    'if __name__ == "__main__":\n'
                    '    print(ask("Hello from Animica!"))\n'
                ),
            }
        # Generic fallback.
        return {"README.md": readme, ".gitignore": gitignore}


__all__ = ["TemplatesService"]
