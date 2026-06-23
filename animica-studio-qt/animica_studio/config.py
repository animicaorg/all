"""Application configuration: :class:`Settings`, :class:`Paths`, persistence.

Settings persist as JSON under the OS-appropriate AppData location (resolved via
PySide6's QStandardPaths when available, with a pure-Python fallback so this
module imports and works headless / without a QApplication).

The API key is treated as a secret: see :func:`redact` and
:meth:`Settings.redacted_dict`.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .models import AutonomyLevel

APP_NAME = "Animica Studio Qt"
ORG_NAME = "Animica"
CONFIG_FILENAME = "settings.json"
DB_FILENAME = "studio.db"

# Built-in agent system prompt (the single source of truth for the agent persona).
DEFAULT_SYSTEM_PROMPT = (
    "You are Animica Studio Agent, an expert autonomous software engineer running "
    "inside Animica Studio Qt. Your job is to build, modify, and FIX real, runnable "
    "applications in the user's open project.\n"
    "\n"
    "Follow this workflow on every coding task:\n"
    "1. UNDERSTAND — inspect before you edit: list_files, read_file/read_files, and "
    "search_project to learn the existing structure, style, and conventions. Never "
    "guess a file's contents — read it.\n"
    "2. PLAN — for anything non-trivial, state a short plan (which files, in what "
    "order) before acting.\n"
    "3. SCAFFOLD — when starting a brand-new app, use scaffold_project with the closest "
    "template instead of hand-writing boilerplate; then customize it.\n"
    "4. IMPLEMENT — make minimal, correct, well-typed changes that match the existing "
    "code style. Create the supporting files a real app needs: dependency manifests "
    "(requirements.txt / package.json), a README, a .gitignore, and at least one test "
    "or smoke check.\n"
    "5. VERIFY — after editing, run run_tests (or run_command) to confirm the app "
    "actually builds/runs. If it fails, read the error, fix the root cause, and verify "
    "again. Do not declare success on code you have not run.\n"
    "6. SUMMARIZE — finish with a short prose summary of what you built/changed, how to "
    "run it, and any follow-ups. End your turn with prose and NO tool call.\n"
    "\n"
    "Rules: request approval for risky actions (installing dependencies, destructive "
    "file or git operations, running shell/docker/deploy/wallet commands); never destroy "
    "files or deploy without explicit approval; never expose, log, or echo secrets, API "
    "keys, or wallet keys; prefer the provided tools over guessing; keep going until the "
    "task is genuinely complete and verified rather than stopping at the first edit."
)

DEFAULT_MODELS = ("anm-fast-8b", "anm-pro-70b")


class ProviderKind(str, Enum):
    """The wire protocol / flavor of the inference endpoint."""

    ANIMICA = "animica"
    OPENAI = "openai"
    OLLAMA = "ollama"

    @classmethod
    def from_value(cls, value: "ProviderKind | str | None") -> "ProviderKind":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.ANIMICA
        try:
            return cls(str(value))
        except ValueError:
            return cls.ANIMICA


# Endpoint presets surfaced in the settings UI.
ENDPOINT_PRESETS: tuple[tuple[str, ProviderKind], ...] = (
    ("https://pool.animica.org/v1", ProviderKind.ANIMICA),
    ("http://localhost:8000/v1", ProviderKind.ANIMICA),
    ("https://api.animica.org/v1", ProviderKind.ANIMICA),
    ("http://localhost:11434/v1", ProviderKind.OLLAMA),
)

# Ecosystem links shown in the sidebar.
ECOSYSTEM_LINKS: tuple[tuple[str, str], ...] = (
    ("Animica", "https://animica.org"),
    ("Pool", "https://pool.animica.org"),
    ("Wallet", "https://wallet.animica.org"),
    ("Chat", "https://chat.animica.org"),
    ("Train", "https://train.animica.org"),
)

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|secret|token|password|passwd|private[_-]?key)"
    r"\s*[:=]?\s*['\"]?([A-Za-z0-9._\-/+=]{6,})"
)


def redact(text: str, *secrets: str) -> str:
    """Redact known secret values and secret-looking key/value pairs from ``text``.

    Pass any concrete secret strings (e.g. the configured API key) to mask them
    even when they appear bare. Always also masks ``key=...``/``token: ...`` pairs.
    """
    if not text:
        return text
    out = text
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    out = _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", out)
    return out


@dataclass
class Paths:
    """Resolved filesystem locations for the application."""

    app_data_dir: Path

    @classmethod
    def resolve(cls) -> "Paths":
        """Resolve the AppData directory via QStandardPaths, falling back gracefully."""
        base: Optional[str] = None
        try:  # Prefer Qt's notion of AppDataLocation when available.
            from PySide6.QtCore import QStandardPaths

            base = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
            # If app metadata is not set, Qt may return a generic dir (e.g.
            # ~/.local/share). Ensure our org/app suffix is present.
            if base and APP_NAME not in base:
                base = os.path.join(base, ORG_NAME, APP_NAME)
        except Exception:
            base = None

        if not base:
            env = os.environ.get("XDG_DATA_HOME")
            if env:
                base = os.path.join(env, ORG_NAME, APP_NAME)
            elif os.name == "nt":
                base = os.path.join(
                    os.environ.get("APPDATA", str(Path.home())), ORG_NAME, APP_NAME
                )
            elif os.uname().sysname == "Darwin" if hasattr(os, "uname") else False:
                base = str(Path.home() / "Library" / "Application Support" / APP_NAME)
            else:
                base = str(Path.home() / ".local" / "share" / ORG_NAME / APP_NAME)

        app_dir = Path(base)
        return cls(app_data_dir=app_dir)

    def ensure(self) -> "Paths":
        """Create the app data directory if missing."""
        self.app_data_dir.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def config_path(self) -> Path:
        return self.app_data_dir / CONFIG_FILENAME

    @property
    def db_path(self) -> Path:
        return self.app_data_dir / DB_FILENAME

    @property
    def snapshots_dir(self) -> Path:
        return self.app_data_dir / "snapshots"

    @property
    def logs_dir(self) -> Path:
        return self.app_data_dir / "logs"


@dataclass
class Settings:
    """User-configurable application settings."""

    endpoint: str = "https://pool.animica.org/v1"
    api_key: str = ""
    model: str = "anm-fast-8b"
    provider_kind: ProviderKind = ProviderKind.ANIMICA
    streaming: bool = True
    temperature: float = 0.3
    # Generous default so a single write_file body (a whole source file) is not
    # truncated mid-output; agentic coding turns routinely need more than 4k.
    max_tokens: int = 8192
    # Generous: ENA inference can run on a CPU-only AICF worker (~2 tok/s) where a
    # full answer takes minutes. 180s timed out mid-generation; 600s lets it
    # complete. Override per-session via ANIMICA_ENA_TIMEOUT (settings_from_env).
    request_timeout: float = 600.0
    system_prompt_override: Optional[str] = None
    autonomy_level: AutonomyLevel = AutonomyLevel.SUGGEST
    theme: str = "dark"
    extra: dict[str, Any] = field(default_factory=dict)

    # -- derived -------------------------------------------------------------
    @property
    def system_prompt(self) -> str:
        """The effective system prompt (override if set, else the built-in default)."""
        override = (self.system_prompt_override or "").strip()
        return override if override else DEFAULT_SYSTEM_PROMPT

    @property
    def chat_completions_url(self) -> str:
        """Full URL of the chat-completions endpoint."""
        base = self.endpoint.rstrip("/")
        return f"{base}/chat/completions"

    @property
    def models_url(self) -> str:
        base = self.endpoint.rstrip("/")
        return f"{base}/models"

    # -- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["provider_kind"] = self.provider_kind.value
        d["autonomy_level"] = self.autonomy_level.value
        return d

    def redacted_dict(self) -> dict[str, Any]:
        """Like :meth:`to_dict` but with the API key masked (safe for logging)."""
        d = self.to_dict()
        if d.get("api_key"):
            d["api_key"] = "***"
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        data = dict(data or {})
        data.pop("api_key_redacted", None)
        provider = ProviderKind.from_value(data.pop("provider_kind", None))
        autonomy = AutonomyLevel.from_value(data.pop("autonomy_level", None))
        known = {
            "endpoint",
            "api_key",
            "model",
            "streaming",
            "temperature",
            "max_tokens",
            "request_timeout",
            "system_prompt_override",
            "theme",
            "extra",
        }
        kwargs = {k: data[k] for k in known if k in data}
        extra = kwargs.get("extra") or {}
        # Stash unrecognized keys in extra rather than dropping them.
        for k, v in data.items():
            if k not in known:
                extra[k] = v
        kwargs["extra"] = extra
        return cls(provider_kind=provider, autonomy_level=autonomy, **kwargs)

    # -- persistence ---------------------------------------------------------
    @classmethod
    def load(cls, paths: Optional[Paths] = None) -> "Settings":
        """Load settings from disk, returning defaults if absent/corrupt."""
        paths = paths or Paths.resolve()
        path = paths.config_path
        try:
            if path.exists():
                with path.open("r", encoding="utf-8") as fh:
                    return cls.from_dict(json.load(fh))
        except Exception:
            pass
        return cls()

    def save(self, paths: Optional[Paths] = None) -> None:
        """Persist settings to disk (creating the app data dir as needed)."""
        paths = (paths or Paths.resolve()).ensure()
        tmp = paths.config_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
        tmp.replace(paths.config_path)


__all__ = [
    "APP_NAME",
    "ORG_NAME",
    "DEFAULT_SYSTEM_PROMPT",
    "DEFAULT_MODELS",
    "ENDPOINT_PRESETS",
    "ECOSYSTEM_LINKS",
    "ProviderKind",
    "Paths",
    "Settings",
    "redact",
]
