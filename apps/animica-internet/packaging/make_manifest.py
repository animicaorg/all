#!/usr/bin/env python3
"""Aggregate per-artifact JSON lines into a downloads manifest.json.

Each per-OS build script (build_linux.sh / build_macos.sh / build_windows.ps1)
appends one JSON object per artifact to a JSON-lines log (default
``dist/artifacts.jsonl``) with the shape::

    {"platform": "...", "name": "AnimicaInternet", "version": "...",
     "filename": "...", "size_bytes": 123, "sha256": "...", "min_os": "..."}

This tool reads one or more such logs and writes a consolidated manifest that
the animica.org downloads/landing page can fetch::

    {
      "generated_at": "2026-07-27T12:00:00Z",
      "app": "AnimicaInternet",
      "version": "0.1.0",
      "artifacts": [
        {
          "platform": "linux",
          "name": "AnimicaInternet",
          "version": "0.1.0",
          "filename": "animica-internet-linux-x86_64.AppImage",
          "download_url": "https://github.com/<repo>/releases/download/<tag>/<filename>",
          "size_bytes": 12345678,
          "sha256": "deadbeef...",
          "min_os": "Ubuntu 22.04+ / glibc 2.35+"
        },
        ...
      ]
    }

The download URL is templated against a GitHub Release. Pass --repo and --tag
(or set GITHUB_REPOSITORY and GITHUB_REF_NAME, which GitHub Actions sets) and a
``{filename}`` placeholder is filled in per artifact. A custom --url-template
may override the default GitHub Releases layout (e.g. to point directly at
``https://animica.org/studio/{filename}`` when artifacts are copied to the
website slot).

Usage:
    make_manifest.py \
        --inputs dist/artifacts.jsonl ... \
        --out downloads/manifest.json \
        --repo animicaorg/all \
        --tag internet-v0.1.0 \
        [--version 0.1.0] \
        [--url-template "https://.../{tag}/{filename}"]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "AnimicaInternet"

DEFAULT_URL_TEMPLATE = (
    "https://github.com/{repo}/releases/download/{tag}/{filename}"
)

# Canonical ordering so the manifest is deterministic regardless of build order.
_PLATFORM_ORDER = {"linux": 0, "macos": 1, "windows": 2}

# Fields surfaced in each manifest entry, in a stable order.
_ENTRY_FIELDS = (
    "platform",
    "name",
    "version",
    "filename",
    "download_url",
    "size_bytes",
    "sha256",
    "min_os",
)


def _utc_now() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _read_jsonl(paths: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for pattern in paths:
        matches = sorted(glob.glob(pattern)) or [pattern]
        for path in matches:
            p = Path(path)
            if not p.exists():
                print(f"[make_manifest] skip missing input: {p}", file=sys.stderr)
                continue
            for lineno, line in enumerate(
                p.read_text(encoding="utf-8-sig").splitlines(), start=1
            ):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(
                        f"[make_manifest] {p}:{lineno}: bad JSON ({exc}); skipping",
                        file=sys.stderr,
                    )
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    return records


def _entry_sort_key(entry: dict[str, Any]) -> tuple[int, str]:
    plat = str(entry.get("platform", ""))
    return (_PLATFORM_ORDER.get(plat, 99), str(entry.get("filename", "")))


def build_manifest(
    records: list[dict[str, Any]],
    *,
    repo: str,
    tag: str,
    url_template: str,
    version: str | None,
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []
    seen_files: set[str] = set()

    for rec in records:
        filename = str(rec.get("filename", "")).strip()
        if not filename:
            print(f"[make_manifest] record without filename: {rec}", file=sys.stderr)
            continue
        if filename in seen_files:
            # Later logs win, so replace the earlier entry.
            artifacts = [a for a in artifacts if a["filename"] != filename]
        seen_files.add(filename)

        download_url = url_template.format(
            repo=repo, tag=tag, filename=filename
        )
        entry = {
            "platform": rec.get("platform", "unknown"),
            "name": rec.get("name", APP_NAME),
            "version": rec.get("version", version or "0.0.0"),
            "filename": filename,
            "download_url": download_url,
            "size_bytes": int(rec.get("size_bytes", 0) or 0),
            "sha256": rec.get("sha256", ""),
            "min_os": rec.get("min_os", ""),
        }
        # Keep only the canonical fields, in canonical order.
        artifacts.append({k: entry[k] for k in _ENTRY_FIELDS})

    artifacts.sort(key=_entry_sort_key)

    # Resolve the top-level version: explicit flag wins, else first artifact.
    resolved_version = version
    if not resolved_version:
        for a in artifacts:
            if a.get("version"):
                resolved_version = a["version"]
                break
    resolved_version = resolved_version or "0.0.0"

    return {
        "generated_at": _utc_now(),
        "app": APP_NAME,
        "version": resolved_version,
        "artifacts": artifacts,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        help="One or more JSON-lines artifact logs (globs allowed).",
    )
    parser.add_argument(
        "--out",
        default="downloads/manifest.json",
        help="Output manifest path (default: downloads/manifest.json).",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY", "animicaorg/all"),
        help="owner/repo for the GitHub Release download URLs.",
    )
    parser.add_argument(
        "--tag",
        default=os.environ.get("GITHUB_REF_NAME", "latest"),
        help="Release tag the artifacts are attached to.",
    )
    parser.add_argument(
        "--version",
        default=os.environ.get("ANIMICA_INTERNET_VERSION"),
        help="Override the top-level manifest version.",
    )
    parser.add_argument(
        "--url-template",
        default=DEFAULT_URL_TEMPLATE,
        help=(
            "Download URL template with {repo} {tag} {filename} placeholders "
            f"(default: {DEFAULT_URL_TEMPLATE})."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    records = _read_jsonl(args.inputs)
    if not records:
        print("[make_manifest] no artifact records found", file=sys.stderr)

    manifest = build_manifest(
        records,
        repo=args.repo,
        tag=args.tag,
        url_template=args.url_template,
        version=args.version,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(
        f"[make_manifest] wrote {out} "
        f"(version={manifest['version']}, {len(manifest['artifacts'])} artifacts)"
    )
    for a in manifest["artifacts"]:
        print(f"  - {a['platform']:8s} {a['filename']}  ({a['size_bytes']} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
