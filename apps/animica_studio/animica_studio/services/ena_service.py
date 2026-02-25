"""ENA provider abstraction + context + patch safety helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import shutil
import subprocess
from typing import Any

import requests
from PySide6.QtCore import QObject, Signal

from animica_studio.services.ena_inference_service import EnaInferenceService, GenerationConfig
from animica_studio.services.ena_model_repository import EnaModelRepository, ModelEntry
from animica_studio.services.ide_service import _safe_path

log = logging.getLogger(__name__)


@dataclass
class EnaResponse:
    text: str
    error: str | None = None


@dataclass
class EnaFileEdit:
    path: str
    original_hash: str
    unified_diff: str


@dataclass
class EnaEditProposal:
    summary: str
    edits: list[EnaFileEdit] = field(default_factory=list)
    error: str | None = None


class EnaProvider(ABC):
    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def capabilities(self) -> dict[str, bool]: ...

    @abstractmethod
    def chat(self, messages: list[dict[str, str]], context: dict[str, Any]) -> EnaResponse: ...

    @abstractmethod
    def propose_edits(self, goal: str, files: dict[str, str], selection: str, context: dict[str, Any]) -> EnaEditProposal: ...

    @abstractmethod
    def analyze_error(self, log_snippet: str, context: dict[str, Any]) -> EnaResponse: ...


class LocalEnaProvider(EnaProvider):
    def is_available(self) -> bool:
        return True

    def capabilities(self) -> dict[str, bool]:
        return {"chat": True, "code_actions": True, "diff": True, "tools": True}

    def chat(self, messages: list[dict[str, str]], context: dict[str, Any]) -> EnaResponse:
        return EnaResponse(text="", error="Legacy local chat provider is disabled. Use EnaIdeAssistantProvider.")

    def propose_edits(self, goal: str, files: dict[str, str], selection: str, context: dict[str, Any]) -> EnaEditProposal:
        if not files:
            return EnaEditProposal(summary="No files in context.", edits=[])
        path, original = next(iter(files.items()))
        append_line = "\n# ENA suggestion: " + goal.strip()
        new_text = original + append_line
        diff = _make_unified_diff(path, original, new_text)
        return EnaEditProposal(
            summary="Added a non-destructive comment suggestion.",
            edits=[EnaFileEdit(path=path, original_hash=_sha256_text(original), unified_diff=diff)],
        )

    def analyze_error(self, log_snippet: str, context: dict[str, Any]) -> EnaResponse:
        return EnaResponse(text=f"ENA(local): Review this error and start from top stack frame:\n{log_snippet[:500]}")


class RemoteEnaProvider(EnaProvider):
    def __init__(self, endpoint: str, api_key: str, model: str) -> None:
        self._endpoint = endpoint.strip()
        self._api_key = api_key.strip()
        self._model = model.strip() or "default"

    def is_available(self) -> bool:
        return bool(self._endpoint)

    def capabilities(self) -> dict[str, bool]:
        return {"chat": True, "code_actions": True, "diff": True, "tools": False}

    def chat(self, messages: list[dict[str, str]], context: dict[str, Any]) -> EnaResponse:
        return self._post_json({"type": "chat", "model": self._model, "messages": messages, "context": context})

    def propose_edits(self, goal: str, files: dict[str, str], selection: str, context: dict[str, Any]) -> EnaEditProposal:
        resp = self._post_json({"type": "propose_edits", "model": self._model, "goal": goal, "files": files, "selection": selection, "context": context})
        if resp.error:
            return EnaEditProposal(summary="", edits=[], error=resp.error)
        try:
            payload = json.loads(resp.text)
            edits = [EnaFileEdit(**e) for e in payload.get("edits", [])]
            return EnaEditProposal(summary=payload.get("summary", "Remote edit proposal"), edits=edits)
        except Exception as exc:  # noqa: BLE001
            return EnaEditProposal(summary="", edits=[], error=str(exc))

    def analyze_error(self, log_snippet: str, context: dict[str, Any]) -> EnaResponse:
        return self._post_json({"type": "analyze_error", "model": self._model, "log": log_snippet, "context": context})

    def _post_json(self, payload: dict[str, Any]) -> EnaResponse:
        if not self._endpoint:
            return EnaResponse(text="", error="ENA endpoint is not configured")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        for _ in range(2):
            try:
                r = requests.post(self._endpoint, headers=headers, json=payload, timeout=(4, 20))
                r.raise_for_status()
                return EnaResponse(text=r.text)
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
        return EnaResponse(text="", error=err)


class EnaIdeAssistantProvider(QObject):
    chunk = Signal(str)
    finished = Signal(bool, dict)
    error = Signal(str, str)

    def __init__(self, ena_config: dict[str, Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ena_config = ena_config
        self._repo = EnaModelRepository()
        self._inference = EnaInferenceService(self)
        self._active_handle: str | None = None
        self._active_parts: list[str] = []
        self._model_cache: list[ModelEntry] = []
        self._inference.chunk.connect(self._on_chunk)
        self._inference.finished.connect(self._on_finished)
        self._inference.error.connect(self.error.emit)

    def selected_model_path(self) -> str:
        ide_cfg = self._ide_cfg()
        return str(ide_cfg.get("model_path") or "").strip()

    def set_selected_model_path(self, model_path: str) -> None:
        ide_cfg = self._ide_cfg()
        ide_cfg["model_path"] = model_path
        self._ena_config["ide_assistant"] = ide_cfg

    def available_status(self) -> tuple[bool, str]:
        model_path = self.selected_model_path()
        if not model_path:
            return False, "No model selected"
        p = Path(model_path).expanduser()
        if not p.exists() or not p.is_file():
            return False, "Missing checkpoint"
        if not _local_inference_backend_ready():
            return False, "Inference backend missing"
        return True, "ready"

    def list_models(self) -> list[ModelEntry]:
        self._model_cache = self._repo.list_models()
        return self._model_cache

    def start(self, prompt: str, history: list[dict[str, str]], generation_cfg: GenerationConfig) -> bool:
        available, _reason = self.available_status()
        if not available:
            return False
        model = self._resolve_model()
        if model is None:
            self.error.emit("Missing model", "No readable local model checkpoint is configured.")
            return False
        self._active_parts = []
        self._active_handle = self._inference.start(prompt, history, model, generation_cfg)
        return True

    def cancel(self) -> None:
        if self._active_handle:
            self._inference.cancel(self._active_handle)
            self._active_handle = None

    def _resolve_model(self) -> ModelEntry | None:
        selected = self.selected_model_path()
        if not selected:
            return None
        for entry in self._model_cache or self._repo.list_models():
            if Path(entry.checkpoint_path).resolve() == Path(selected).expanduser().resolve():
                return entry
        p = Path(selected).expanduser()
        if p.exists() and p.is_file():
            return ModelEntry(name=p.stem, checkpoint_path=str(p.resolve()), created_at=p.stat().st_mtime)
        return None

    def _ide_cfg(self) -> dict[str, Any]:
        raw = self._ena_config.get("ide_assistant")
        return dict(raw) if isinstance(raw, dict) else {}

    def _on_chunk(self, text: str) -> None:
        self._active_parts.append(text)
        self.chunk.emit(text)

    def _on_finished(self, ok: bool, stats: dict[str, Any]) -> None:
        assembled = "".join(self._active_parts).strip()
        self._active_handle = None
        out = dict(stats)
        if assembled and not out.get("text"):
            out["text"] = assembled
        self.finished.emit(ok, out)


def _local_inference_backend_ready() -> bool:
    if importlib.util.find_spec("ena.inference") is not None:
        return True
    exe = shutil.which("ena")
    if not exe:
        return False
    try:
        probe = subprocess.run([exe, "infer", "--help"], capture_output=True, text=True, timeout=2, check=False)
        return probe.returncode == 0
    except Exception:  # noqa: BLE001
        return False


class WorkspaceIndexService:
    def __init__(self, max_file_bytes: int = 200_000, max_total_bytes: int = 1_000_000) -> None:
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes

    def list_workspace_files(self, workspace: Path) -> list[Path]:
        ignored = {".git", "node_modules", "dist", "venv", ".venv", "__pycache__"}
        out: list[Path] = []
        for p in workspace.rglob("*"):
            if any(part in ignored for part in p.parts):
                continue
            if p.is_file():
                out.append(p)
        return out

    def read_files(self, workspace: Path, rel_paths: list[str]) -> dict[str, str]:
        total = 0
        out: dict[str, str] = {}
        for rel in rel_paths:
            p = _safe_path(workspace, rel)
            if p is None or not p.exists() or not p.is_file():
                continue
            size = p.stat().st_size
            if size > self.max_file_bytes:
                continue
            total += size
            if total > self.max_total_bytes:
                break
            out[rel] = p.read_text(encoding="utf-8", errors="replace")
        return out


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _make_unified_diff(path: str, old: str, new: str) -> str:
    import difflib

    return "\n".join(
        difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile=path, tofile=path, lineterm="")
    )


def apply_edit_atomic(workspace: Path, edit: EnaFileEdit) -> None:
    target = _safe_path(workspace, edit.path)
    if target is None or not target.exists():
        raise FileNotFoundError(edit.path)
    current = target.read_text(encoding="utf-8", errors="replace")
    if _sha256_text(current) != edit.original_hash:
        raise ValueError(f"Hash mismatch for {edit.path}")
    new_text = _apply_unified_diff(current, edit.unified_diff)
    tmp = target.with_suffix(target.suffix + ".ena_tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(target)


def _apply_unified_diff(original: str, diff_text: str) -> str:
    old_lines = original.splitlines()
    new_lines: list[str] = []
    idx = 0
    for line in diff_text.splitlines():
        if not line or line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith(" "):
            if idx < len(old_lines):
                new_lines.append(old_lines[idx])
            idx += 1
        elif line.startswith("-"):
            idx += 1
        elif line.startswith("+"):
            new_lines.append(line[1:])
    if idx < len(old_lines):
        new_lines.extend(old_lines[idx:])
    return "\n".join(new_lines) + ("\n" if original.endswith("\n") else "")


def command_is_allowed(cmd: list[str], allowlist: list[str]) -> bool:
    if not cmd:
        return False
    rendered = " ".join(cmd)
    banned = [" rm ", "curl", "wget", "| sh", "> /", "sudo "]
    for token in banned:
        if token in f" {rendered} ":
            return False
    return any(rendered.startswith(prefix) for prefix in allowlist)


def run_tool_command(cmd: list[str], cwd: str, timeout_s: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s, check=False)
