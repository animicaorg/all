#!/usr/bin/env python3
"""
Animica zk.registry.list_circuits
=================================

Pretty-printer for the zk registry, intended for docs / explorers.

It merges:
- zk/registry/registry.yaml  (metadata: kinds, circuits)
- zk/registry/vk_cache.json  (VK material: vk, fri_params, vk_hash, optional signatures)

and renders an easy-to-read table / markdown / JSON.

Features
--------
- Status per circuit: OK / MISSING / STALE (hash mismatch)
- Shows kind → scheme/curve (from kinds section)
- Detects signature presence (does not verify here)
- Filters: by kind, only missing, only stale
- Formats: table (default), markdown, json, ndjson
- Strict mode: non-zero exit code if any missing/stale entries

Usage
-----
# Default table
python -m zk.registry.list_circuits

# Markdown table for docs
python -m zk.registry.list_circuits --format markdown

# Only entries with issues
python -m zk.registry.list_circuits --only-missing --only-stale

# Filter by kind
python -m zk.registry.list_circuits --kind plonk_kzg_bn254

# JSON dump
python -m zk.registry.list_circuits --format json

License: MIT
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from hashlib import sha3_256
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Local files (relative to this script)
HERE = Path(__file__).resolve().parent
REGISTRY_YAML_PATH = HERE / "registry.yaml"
VK_CACHE_PATH = HERE / "vk_cache.json"

# Optional YAML dependency
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _try_load_yaml_or_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    # If file *is* JSON, prefer JSON parser (no extra deps needed)
    if text.lstrip().startswith("{"):
        return json.loads(text)
    if yaml is None:
        raise SystemExit(
            f"{path} looks like YAML (not JSON) but PyYAML is not installed.\n"
            "Install with: pip install pyyaml"
        )
    return yaml.safe_load(text) or {}


def _load_registry_yaml(path: Path = REGISTRY_YAML_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {}
    data = _try_load_yaml_or_json(path)
    # Normalize expected sections
    data.setdefault("kinds", {})
    data.setdefault("circuits", {})
    return data


def _load_vk_cache(path: Path = VK_CACHE_PATH) -> Dict[str, Any]:
    if not path.exists():
        return {"schema_version": "1", "entries": {}}
    cache = _load_json(path)
    cache.setdefault("entries", {})
    return cache


# ---------------------------------------------------------------------------
# Hashing logic (must match updater)
# ---------------------------------------------------------------------------


def _canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha3_256_hex(data: bytes) -> str:
    return sha3_256(data).hexdigest()


def _compute_vk_hash(entry: Dict[str, Any]) -> str:
    """
    Compute canonical hash over the VK *material* (same as update_vk.py):
      - kind
      - vk_format
      - vk (if present)
      - fri_params (if present)
    """
    payload = {
        "kind": entry.get("kind"),
        "vk_format": entry.get("vk_format"),
        "vk": entry.get("vk", None),
        "fri_params": entry.get("fri_params", None),
    }
    return f"sha3-256:{_sha3_256_hex(_canonical_json_bytes(payload))}"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class KindMeta:
    kind: str
    scheme: str = ""
    curve: str = ""
    transcript_hash: str = ""
    vk_format: str = ""
    version: str = ""


@dataclass
class CircuitRow:
    circuit_id: str
    kind: str
    scheme: str
    curve_or_field: str
    vk_hash_short: str
    signed: bool
    status: str  # OK | MISSING | STALE
    version: str

    def as_markdown_row(self) -> str:
        return f"| `{self.circuit_id}` | `{self.kind}` | {self.scheme} | {self.curve_or_field} | `{self.vk_hash_short}` | {('yes' if self.signed else 'no')} | **{self.status}** | {self.version} |"

    def as_table_tuple(self) -> Tuple[str, str, str, str, str, str, str, str]:
        return (
            self.circuit_id,
            self.kind,
            self.scheme,
            self.curve_or_field,
            self.vk_hash_short,
            "yes" if self.signed else "no",
            self.status,
            self.version,
        )


# ---------------------------------------------------------------------------
# Core merge logic
# ---------------------------------------------------------------------------


def _build_kind_meta(kinds: Dict[str, Any]) -> Dict[str, KindMeta]:
    out: Dict[str, KindMeta] = {}
    for k, v in (kinds or {}).items():
        scheme = str(v.get("scheme", ""))
        curve = str(v.get("curve", "")) or str((v.get("field") or {}).get("name", ""))
        th = str((v.get("transcript") or {}).get("hash", ""))
        vkf = str(v.get("vk_format", ""))
        ver = str(v.get("version", ""))
        out[k] = KindMeta(
            kind=k,
            scheme=scheme,
            curve=curve,
            transcript_hash=th,
            vk_format=vkf,
            version=ver,
        )
    return out


def _status_for(entry: Optional[Dict[str, Any]]) -> Tuple[str, str, bool]:
    """
    Returns (status, short_hash, signed)
    """
    if entry is None:
        return ("MISSING", "—", False)
    stored_hash = entry.get("vk_hash") or "—"
    short = (
        stored_hash.split(":")[-1][:8]
        if isinstance(stored_hash, str) and stored_hash != "none"
        else "—"
    )
    recomputed = _compute_vk_hash(entry)
    status = "OK" if stored_hash == recomputed else "STALE"
    signed = bool(entry.get("sig"))
    return (status, short, signed)


def _gather_rows(registry: Dict[str, Any], cache: Dict[str, Any]) -> List[CircuitRow]:
    kinds_meta = _build_kind_meta(registry.get("kinds", {}))
    circuits = registry.get("circuits", {})
    cache_entries = (cache or {}).get("entries", {})

    rows: List[CircuitRow] = []

    # Known circuits from registry.yaml
    for cid, meta in circuits.items():
        kind = str(meta.get("kind", ""))
        km = kinds_meta.get(kind, KindMeta(kind=kind))
        vk_key = str(meta.get("vk_cache_key", cid))
        entry = cache_entries.get(vk_key)
        status, short_hash, signed = _status_for(entry)
        rows.append(
            CircuitRow(
                circuit_id=cid,
                kind=kind,
                scheme=km.scheme,
                curve_or_field=km.curve
                or km.scheme,  # if curve empty for STARK, leave scheme
                vk_hash_short=short_hash,
                signed=signed,
                status=status,
                version=str(meta.get("version", "")),
            )
        )

    # Any extra entries in cache not referenced by registry.yaml
    for vk_key, entry in cache_entries.items():
        # skip if present via circuits mapping
        if vk_key in circuits or any(
            v.get("vk_cache_key") == vk_key for v in circuits.values()
        ):
            continue
        kind = str(entry.get("kind", ""))
        km = kinds_meta.get(kind, KindMeta(kind=kind))
        status, short_hash, signed = _status_for(entry)
        rows.append(
            CircuitRow(
                circuit_id=vk_key,
                kind=kind,
                scheme=km.scheme,
                curve_or_field=km.curve or km.scheme,
                vk_hash_short=short_hash,
                signed=signed,
                status=status,
                version="",  # unknown
            )
        )

    # Sort by circuit_id
    rows.sort(key=lambda r: r.circuit_id)
    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_table(rows: List[CircuitRow]) -> str:
    headers = (
        "CIRCUIT_ID",
        "KIND",
        "SCHEME",
        "CURVE/FIELD",
        "VK_HASH",
        "SIGNED",
        "STATUS",
        "VER",
    )
    data = [r.as_table_tuple() for r in rows]
    widths = [len(h) for h in headers]
    for row in data:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: Tuple[str, ...]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    lines = [fmt_row(headers), fmt_row(tuple("-" * w for w in widths))]
    for row in data:
        lines.append(fmt_row(row))
    return "\n".join(lines)


def _render_markdown(rows: List[CircuitRow]) -> str:
    head = "| CIRCUIT_ID | KIND | SCHEME | CURVE/FIELD | VK_HASH | SIGNED | STATUS | VER |\n|---|---|---|---|---|---|---|---|"
    body = "\n".join(r.as_markdown_row() for r in rows)
    return f"{head}\n{body}"


def _render_json(rows: List[CircuitRow]) -> str:
    return json.dumps([asdict(r) for r in rows], indent=2, sort_keys=True)


def _render_ndjson(rows: List[CircuitRow]) -> str:
    return "\n".join(json.dumps(asdict(r), sort_keys=True) for r in rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Pretty-print zk registry circuits")
    p.add_argument(
        "--format", choices=("table", "markdown", "json", "ndjson"), default="table"
    )
    p.add_argument("--kind", help="Filter by verifier kind (e.g., groth16_bn254)")
    p.add_argument(
        "--only-missing", action="store_true", help="Show only circuits without VKs"
    )
    p.add_argument(
        "--only-stale",
        action="store_true",
        help="Show only circuits with hash mismatch",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any missing/stale entries",
    )
    p.add_argument(
        "--registry",
        type=Path,
        default=REGISTRY_YAML_PATH,
        help="Path to registry.yaml",
    )
    p.add_argument(
        "--vk-cache", type=Path, default=VK_CACHE_PATH, help="Path to vk_cache.json"
    )
    return p


def main(argv: Optional[List[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    registry = _load_registry_yaml(args.registry)
    cache = _load_vk_cache(args.vk_cache)

    rows = _gather_rows(registry, cache)

    # Filters
    if args.kind:
        rows = [r for r in rows if r.kind == args.kind]
    if args.only_missing:
        rows = [r for r in rows if r.status == "MISSING"]
    if args.only_stale:
        rows = [r for r in rows if r.status == "STALE"]

    if args.format == "table":
        out = _render_table(rows)
    elif args.format == "markdown":
        out = _render_markdown(rows)
    elif args.format == "json":
        out = _render_json(rows)
    else:
        out = _render_ndjson(rows)

    print(out)

    if args.strict:
        if any(r.status in ("MISSING", "STALE") for r in rows):
            raise SystemExit(2)


if __name__ == "__main__":
    main()
