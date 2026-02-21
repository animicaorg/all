"""CLI capability discovery and cached registry for Animica Studio."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from animica_studio.services.cli_runner import CliRunner
from animica_studio.services.job_runner import resolve_animica_cli_program_and_env
from animica_studio.storage.config import Config
from animica_studio.util.paths import app_data_dir

log = logging.getLogger(__name__)


def _norm_path(path: list[str]) -> str:
    return " ".join(path)


def _parse_block(help_text: str, header: str) -> list[str]:
    out: list[str] = []
    in_block = False
    for line in help_text.splitlines():
        stripped = line.rstrip()
        if not stripped.strip():
            if in_block:
                break
            continue
        if stripped.strip().lower().startswith(header):
            in_block = True
            continue
        if not in_block:
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            break
        token = stripped.strip().split()[0].rstrip(",")
        if token:
            out.append(token)
    return out


def _parse_commands(help_text: str) -> list[str]:
    return [c.rstrip(":") for c in _parse_block(help_text, "commands:") if c and c[0].isalnum()]


_OPT_RE = re.compile(r"(--[a-zA-Z0-9][a-zA-Z0-9\-_]*)")


def _parse_options(help_text: str) -> set[str]:
    opts: set[str] = set()
    for line in _parse_block(help_text, "options:"):
        for match in _OPT_RE.findall(line):
            opts.add(match)
    # fallback for compact help formats
    if not opts:
        opts.update(_OPT_RE.findall(help_text))
    return opts


@dataclass
class CliNode:
    commands: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    raw_help: str = ""
    unknown: bool = False


class CliRegistry:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._runner = CliRunner()
        self._nodes: dict[str, CliNode] = {}
        self._cli_path: str = ""
        self._loaded_at: float | None = None
        self._registry_path = app_data_dir() / "cli_registry.json"
        self.load()

    @property
    def cli_path(self) -> str:
        return self._cli_path

    def load(self) -> None:
        if not self._registry_path.exists():
            return
        try:
            payload = json.loads(self._registry_path.read_text(encoding="utf-8"))
            self._cli_path = str(payload.get("cli_path", ""))
            self._loaded_at = payload.get("loaded_at")
            nodes = payload.get("nodes", {})
            self._nodes = {k: CliNode(**v) for k, v in nodes.items() if isinstance(v, dict)}
        except Exception as exc:  # noqa: BLE001
            log.warning("CliRegistry: failed to load cache: %s", exc)

    def save(self) -> None:
        payload = {
            "cli_path": self._cli_path,
            "loaded_at": self._loaded_at,
            "nodes": {k: vars(v) for k, v in self._nodes.items()},
        }
        self._registry_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def refresh(self) -> None:
        self._nodes = {}
        try:
            program, base_args, _env = resolve_animica_cli_program_and_env(self._config)
        except FileNotFoundError:
            self._loaded_at = time.time()
            return

        self._cli_path = program
        root = self._run_help([program, *base_args, "--help"])
        self._nodes[""] = root
        top_level = root.commands
        for cmd in top_level:
            node = self._run_help([program, *base_args, cmd, "--help"])
            key = _norm_path([cmd])
            self._nodes[key] = node
            for sub in node.commands:
                sub_node = self._run_help([program, *base_args, cmd, sub, "--help"])
                self._nodes[_norm_path([cmd, sub])] = sub_node

        self._loaded_at = time.time()
        self.save()

    def _run_help(self, argv: list[str]) -> CliNode:
        result = self._runner.run(argv, timeout_s=5.0)
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        try:
            commands = _parse_commands(text)
            options = sorted(_parse_options(text))
            return CliNode(commands=commands, options=options, raw_help=text, unknown=False)
        except Exception:
            return CliNode(raw_help=text, unknown=True)

    def has_cmd(self, path: list[str]) -> bool:
        if not self._nodes:
            return False
        if not path:
            return True
        key = _norm_path(path)
        if key in self._nodes:
            return True
        parent = _norm_path(path[:-1])
        parent_node = self._nodes.get(parent)
        return bool(parent_node and path[-1] in parent_node.commands)

    def has_opt(self, path: list[str], option: str) -> bool:
        node = self._nodes.get(_norm_path(path))
        return bool(node and option in node.options)

    def best_match(self, group: str) -> list[str]:
        aliases = {
            "mine_blocks": [["miner", "mine-blocks"], ["miner", "mine_blocks"], ["mine", "blocks"], ["mine-blocks"]],
            "wallet_create": [["wallet", "create"]],
            "aicf_status": [["aicf", "status"]],
            "aicf_jobs_watch": [["aicf", "jobs", "watch"], ["aicf", "watch"]],
            "wallet_list": [["wallet", "list"]],
        }
        for candidate in aliases.get(group, []):
            if self.has_cmd(candidate):
                return candidate
        return []


    def top_level_commands(self) -> list[str]:
        node = self._nodes.get("")
        return list(node.commands) if node else []

    def options_for(self, path: list[str]) -> list[str]:
        node = self._nodes.get(_norm_path(path))
        return list(node.options) if node else []

    def diagnostics(self, path: list[str]) -> str:
        lines = [f"CLI path: {self._cli_path or '<unknown>'}"]
        root = self._nodes.get("")
        if root:
            lines.append("\n$ animica --help")
            lines.append(root.raw_help)
        key = _norm_path(path)
        node = self._nodes.get(key)
        if node:
            lines.append(f"\n$ animica {key} --help")
            lines.append(node.raw_help)
        return "\n".join(lines)

