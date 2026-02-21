"""Template loading/rendering for IDE script generation."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import re
from pathlib import Path
from typing import Any

from animica_studio.util.paths import app_data_dir

log = logging.getLogger(__name__)


@dataclass
class PlaceholderDef:
    key: str
    label: str
    default: str = ""
    required: bool = False
    validation_regex: str | None = None
    help_text: str = ""


@dataclass
class TemplateDef:
    id: str
    name: str
    category: str
    description: str
    tags: list[str] = field(default_factory=list)
    language: str = "python"
    placeholders: list[PlaceholderDef] = field(default_factory=list)
    post_create_actions: list[str] = field(default_factory=list)
    default_filename: str = "new_script.py"
    content_path: Path | None = None


class TemplateService:
    def __init__(self, user_templates_dir: str | None = None) -> None:
        self._templates: dict[str, TemplateDef] = {}
        self._user_templates_dir_override = user_templates_dir

    @property
    def user_templates_dir(self) -> Path:
        if self._user_templates_dir_override:
            d = Path(self._user_templates_dir_override).expanduser()
            d.mkdir(parents=True, exist_ok=True)
            return d
        if Path.home().joinpath("Library").exists() and Path.home().joinpath("Library", "Application Support").exists():
            mac = Path.home() / "Library" / "Application Support" / "animica-studio" / "templates"
            mac.mkdir(parents=True, exist_ok=True)
            return mac
        d = app_data_dir() / "templates"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def load_builtin_templates(self) -> None:
        root = Path(__file__).resolve().parent.parent / "resources" / "templates"
        self._load_templates_from_root(root)

    def load_user_templates(self) -> None:
        self._load_templates_from_root(self.user_templates_dir)

    def _load_templates_from_root(self, root: Path) -> None:
        if not root.exists():
            return
        for meta_path in root.glob("*/*/template.json"):
            try:
                template = self._parse_template(meta_path)
                self._templates[template.id] = template
            except Exception as exc:  # noqa: BLE001
                log.warning("Template load failed (%s): %s", meta_path, exc)

    def _parse_template(self, meta_path: Path) -> TemplateDef:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        content_path = meta_path.with_name("content.py")
        placeholders = [PlaceholderDef(**p) for p in raw.get("placeholders", [])]
        return TemplateDef(
            id=str(raw["id"]),
            name=str(raw.get("name", raw["id"])),
            category=str(raw.get("category", "General")),
            description=str(raw.get("description", "")),
            tags=[str(t) for t in raw.get("tags", [])],
            language=str(raw.get("language", "python")),
            placeholders=placeholders,
            post_create_actions=[str(x) for x in raw.get("post_create_actions", [])],
            default_filename=str(raw.get("default_filename", f"{raw['id']}.py")),
            content_path=content_path,
        )

    def list_templates(self, query: str = "", category: str | None = None) -> list[TemplateDef]:
        q = query.strip().lower()
        templates = list(self._templates.values())
        if category and category != "All":
            templates = [t for t in templates if t.category == category]
        if q:
            templates = [
                t
                for t in templates
                if q in t.name.lower() or q in t.description.lower() or any(q in tag.lower() for tag in t.tags)
            ]
        return sorted(templates, key=lambda t: (t.category.lower(), t.name.lower()))

    def get(self, template_id: str) -> TemplateDef:
        return self._templates[template_id]

    def render(self, template_id: str, params: dict[str, str]) -> str:
        template = self.get(template_id)
        values: dict[str, str] = {}
        for placeholder in template.placeholders:
            value = (params.get(placeholder.key) or placeholder.default or "").strip()
            if placeholder.required and not value:
                raise ValueError(f"{placeholder.label} is required")
            if placeholder.validation_regex and value:
                if re.fullmatch(placeholder.validation_regex, value) is None:
                    raise ValueError(f"{placeholder.label} is invalid")
            values[placeholder.key] = value
        if template.content_path is None or not template.content_path.exists():
            raise FileNotFoundError(f"Missing template content for {template_id}")
        text = template.content_path.read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", value)
        return text

    def categories(self) -> list[str]:
        return sorted({t.category for t in self._templates.values()})
