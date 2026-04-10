from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union


ManifestLike = Union[str, Path, Mapping[str, Any]]


@dataclass(frozen=True)
class ResolvedContractSource:
    manifest: Dict[str, Any]
    manifest_path: Optional[Path]
    manifest_dir: Path
    source_text: str
    source_paths: Tuple[Path, ...]
    selected_field: str
    selected_value: Any
    entry: Optional[str]


def load_manifest_input(m: ManifestLike) -> Tuple[Dict[str, Any], Optional[Path]]:
    """Load manifest input from path or mapping and return (manifest, manifest_path)."""
    if isinstance(m, (str, Path)):
        path = Path(m).resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"manifest not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON in manifest {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("manifest root must be an object")
        return dict(data), path

    if isinstance(m, Mapping):
        return dict(m), None

    raise TypeError(f"unsupported manifest input type: {type(m).__name__}")


def _looks_like_inline_source(text: str) -> bool:
    stripped = text.lstrip()
    if "\n" in text:
        return True
    inline_prefixes = (
        "def ",
        "from ",
        "import ",
        "class ",
        "if ",
        "#",
        '"""',
        "'''",
        "@",
    )
    return stripped.startswith(inline_prefixes)


def _resolve_fs_path(value: str, manifest_dir: Path) -> Path:
    """Resolve source path primarily relative to manifest dir, with cwd fallback."""
    raw = Path(value)
    if raw.is_absolute():
        return raw

    manifest_rel = (manifest_dir / raw).resolve()
    if manifest_rel.exists():
        return manifest_rel

    # Fallback for repo/cwd-relative values to avoid accidental double-prefixing.
    cwd_rel = raw.resolve()
    if cwd_rel.exists():
        return cwd_rel

    return manifest_rel


def _read_source_value(
    value: Any,
    *,
    manifest_dir: Path,
    field_name: str,
) -> Tuple[str, Tuple[Path, ...], Any]:
    """Resolve a manifest source value into source text and zero-or-more source paths."""
    if isinstance(value, str):
        path = _resolve_fs_path(value, manifest_dir)
        if path.exists():
            return path.read_text(encoding="utf-8"), (path,), value
        if _looks_like_inline_source(value):
            return value, (), value
        raise FileNotFoundError(
            f"{field_name} references missing file: {value!r} (resolved: {path})"
        )

    if isinstance(value, Mapping):
        for key in ("code", "content", "inline"):
            inline = value.get(key)
            if isinstance(inline, str):
                return inline, (), value

        for key in ("path", "file", "entry"):
            src_path = value.get(key)
            if isinstance(src_path, str):
                path = _resolve_fs_path(src_path, manifest_dir)
                if not path.exists():
                    raise FileNotFoundError(
                        f"{field_name}.{key} references missing file: {src_path!r} (resolved: {path})"
                    )
                return path.read_text(encoding="utf-8"), (path,), value

        raise ValueError(
            f"{field_name} mapping must contain one of: code/content/inline/path/file/entry"
        )

    raise TypeError(f"{field_name} must be string or object, got {type(value).__name__}")


def _resolve_entry_from_sources(
    entry: str,
    sources: Any,
    *,
    manifest_dir: Path,
) -> Optional[Tuple[str, Tuple[Path, ...], Any]]:
    if isinstance(sources, Mapping):
        aliases = (
            entry,
            entry.lstrip("./"),
            f"./{entry.lstrip('./')}",
        )
        for key in aliases:
            if key in sources:
                return _read_source_value(
                    sources[key],
                    manifest_dir=manifest_dir,
                    field_name=f"sources[{key!r}]",
                )
        return None

    if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes, bytearray)):
        # If sources is a list, entry should still resolve as a manifest-dir path.
        return None

    return None


def _concat_sources_list(
    *,
    values: Sequence[Any],
    manifest_dir: Path,
    field_name: str,
) -> Tuple[str, Tuple[Path, ...], Any]:
    chunks = []
    paths = []
    for idx, item in enumerate(values):
        text, src_paths, _selected = _read_source_value(
            item,
            manifest_dir=manifest_dir,
            field_name=f"{field_name}[{idx}]",
        )
        if src_paths:
            paths.extend(src_paths)
            header = f"# ---- {field_name}[{idx}]: {src_paths[0]} ----\n"
            chunks.append(header + text + "\n")
        else:
            header = f"# ---- {field_name}[{idx}] (inline) ----\n"
            chunks.append(header + text + "\n")
    return "".join(chunks), tuple(paths), list(values)


def resolve_contract_source(
    manifest: Mapping[str, Any],
    *,
    manifest_path: Optional[Union[str, Path]] = None,
    base_dir: Optional[Union[str, Path]] = None,
) -> ResolvedContractSource:
    """
    Resolve manifest source with explicit, stable precedence:
      1) source
      2) entry (with sources map support)
      3) code
      4) path
      5) sources (list/dict fallback)
    """
    if not isinstance(manifest, Mapping):
        raise TypeError("manifest must be a mapping")

    man = dict(manifest)
    man_path = Path(manifest_path).resolve() if manifest_path is not None else None
    if base_dir is not None:
        manifest_dir = Path(base_dir).resolve()
    elif man_path is not None:
        manifest_dir = man_path.parent.resolve()
    else:
        manifest_dir = Path.cwd().resolve()

    # 1) source
    if "source" in man and man.get("source") not in (None, ""):
        text, paths, selected = _read_source_value(
            man["source"],
            manifest_dir=manifest_dir,
            field_name="source",
        )
        return ResolvedContractSource(
            manifest=man,
            manifest_path=man_path,
            manifest_dir=manifest_dir,
            source_text=text,
            source_paths=paths,
            selected_field="source",
            selected_value=selected,
            entry=man.get("entry") if isinstance(man.get("entry"), str) else None,
        )

    # 2) entry (+ optional sources dict indirection)
    entry = man.get("entry")
    if not isinstance(entry, str) or not entry.strip():
        entry = man.get("entrypoint") if isinstance(man.get("entrypoint"), str) else None

    sources = man.get("sources")
    if isinstance(entry, str) and entry.strip():
        resolved = _resolve_entry_from_sources(entry, sources, manifest_dir=manifest_dir)
        if resolved is not None:
            text, paths, selected = resolved
            return ResolvedContractSource(
                manifest=man,
                manifest_path=man_path,
                manifest_dir=manifest_dir,
                source_text=text,
                source_paths=paths,
                selected_field="entry",
                selected_value=selected,
                entry=entry,
            )

        text, paths, selected = _read_source_value(
            entry,
            manifest_dir=manifest_dir,
            field_name="entry",
        )
        return ResolvedContractSource(
            manifest=man,
            manifest_path=man_path,
            manifest_dir=manifest_dir,
            source_text=text,
            source_paths=paths,
            selected_field="entry",
            selected_value=selected,
            entry=entry,
        )

    # 3/4) legacy `code` and `path`
    for legacy_field in ("code", "path"):
        if legacy_field in man and man.get(legacy_field) not in (None, ""):
            text, paths, selected = _read_source_value(
                man[legacy_field],
                manifest_dir=manifest_dir,
                field_name=legacy_field,
            )
            return ResolvedContractSource(
                manifest=man,
                manifest_path=man_path,
                manifest_dir=manifest_dir,
                source_text=text,
                source_paths=paths,
                selected_field=legacy_field,
                selected_value=selected,
                entry=None,
            )

    # 5) sources fallback
    if isinstance(sources, Sequence) and not isinstance(sources, (str, bytes, bytearray)):
        text, paths, selected = _concat_sources_list(
            values=list(sources),
            manifest_dir=manifest_dir,
            field_name="sources",
        )
        return ResolvedContractSource(
            manifest=man,
            manifest_path=man_path,
            manifest_dir=manifest_dir,
            source_text=text,
            source_paths=paths,
            selected_field="sources",
            selected_value=selected,
            entry=None,
        )

    if isinstance(sources, Mapping):
        if "main" in sources:
            text, paths, selected = _read_source_value(
                sources["main"],
                manifest_dir=manifest_dir,
                field_name="sources['main']",
            )
            return ResolvedContractSource(
                manifest=man,
                manifest_path=man_path,
                manifest_dir=manifest_dir,
                source_text=text,
                source_paths=paths,
                selected_field="sources",
                selected_value=selected,
                entry="main",
            )

        # Deterministic fallback: first key in sorted order.
        if sources:
            first_key = sorted(str(k) for k in sources.keys())[0]
            text, paths, selected = _read_source_value(
                sources[first_key],
                manifest_dir=manifest_dir,
                field_name=f"sources[{first_key!r}]",
            )
            return ResolvedContractSource(
                manifest=man,
                manifest_path=man_path,
                manifest_dir=manifest_dir,
                source_text=text,
                source_paths=paths,
                selected_field="sources",
                selected_value=selected,
                entry=first_key,
            )

    raise ValueError(
        "manifest must declare contract source via one of: source, entry, code, path, or sources"
    )


__all__ = [
    "ManifestLike",
    "ResolvedContractSource",
    "load_manifest_input",
    "resolve_contract_source",
]
