"""Data models for the Console service."""
from __future__ import annotations
import uuid
from dataclasses import dataclass
from typing import Any

@dataclass
class CommandPreset:
    id: str
    group: str
    label: str
    argv: list[str]
    needs_profile: bool = False
    dangerous: bool = False
    confirm: bool = False

    @staticmethod
    def make(group: str, label: str, argv: list[str], **kwargs: Any) -> "CommandPreset":
        return CommandPreset(id=str(uuid.uuid4()), group=group, label=label, argv=argv, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "group": self.group, "label": self.label,
            "argv": self.argv, "needs_profile": self.needs_profile,
            "dangerous": self.dangerous, "confirm": self.confirm,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "CommandPreset":
        return CommandPreset(
            id=str(d.get("id", str(uuid.uuid4()))),
            group=str(d.get("group", "Other")),
            label=str(d.get("label", "")),
            argv=list(d.get("argv", [])),
            needs_profile=bool(d.get("needs_profile", False)),
            dangerous=bool(d.get("dangerous", False)),
            confirm=bool(d.get("confirm", False)),
        )


@dataclass
class RunRecord:
    id: str
    started_ts: float
    ended_ts: float
    argv: list[str]
    cwd: str | None
    profile_name: str | None
    exit_code: int | None
    duration_ms: int
    cancelled: bool
    error: str | None
    stdout_snippet: str = ""  # last 500 chars of stdout

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.cancelled and self.error is None

    @staticmethod
    def make_id() -> str:
        return str(uuid.uuid4())
