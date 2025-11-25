#!/usr/bin/env python3
"""
fetch_circom_artifacts.py

Downloads a small set of prebuilt Circom artifacts (verifying keys and sample proofs)
into the repository tree so tests and examples don't depend on a local Circom install.

Defaults assume a mirror layout like:

  <BASE>/
    groth16/embedding/{vk.json,proof.json,public.json}
    groth16/storage_porep_stub/{vk.json,proof.json,public.json}
    plonk_kzg/poseidon_hash/{vk.json,proof.json,public.json}

You can override the base mirror with:
  - CLI:  --base-url https://example.com/animica/zk
  - ENV:  ANIMICA_CIRCOM_BASE=https://example.com/animica/zk

If a file already exists and matches the recorded checksum (when provided), it will be
skipped unless --force is passed.

This script is intentionally stdlib-only.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import hashlib
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# -----------------------
# Manifest
# -----------------------

@dataclass(frozen=True)
class FileItem:
    name: str  # e.g. "vk.json"
    sha256: Optional[str] = None  # hex string; if None, checksum is not enforced


@dataclass(frozen=True)
class Artifact:
    system: str   # "groth16" | "plonk_kzg"
    circuit: str  # e.g. "embedding"
    files: tuple[FileItem, ...]


# NOTE: Checksums are optional here and may be added over time. When provided,
# they will be enforced. If your mirror hosts different content, pass --force
# or omit checksums locally.
ARTIFACTS: tuple[Artifact, ...] = (
    Artifact(
        system="groth16",
        circuit="embedding",
        files=(
            FileItem("vk.json"),
            FileItem("proof.json"),
            FileItem("public.json"),
        ),
    ),
    Artifact(
        system="groth16",
        circuit="storage_porep_stub",
        files=(
            FileItem("vk.json"),
            FileItem("proof.json"),
            FileItem("public.json"),
        ),
    ),
    Artifact(
        system="plonk_kzg",
        circuit="poseidon_hash",
        files=(
            FileItem("vk.json"),
            FileItem("proof.json"),
            FileItem("public.json"),
        ),
    ),
)


# -----------------------
# Utils
# -----------------------

def human_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    for u in units:
        if n < 1024 or u == units[-1]:
            return f"{n:.0f} {u}"
        n /= 1024
    return f"{n:.0f} B"


def sha256_hex(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mkdirp(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def matches_filter(artifact: Artifact, only: Optional[str]) -> bool:
    if not only:
        return True
    # Accept forms like "groth16/embedding" or substring matches.
    key = f"{artifact.system}/{artifact.circuit}"
    return only in key


def _default_base_url() -> str:
    # You can set a default mirror here if you have one. Otherwise, users can
    # provide --base-url or ANIMICA_CIRCOM_BASE at runtime.
    return os.environ.get("ANIMICA_CIRCOM_BASE", "").strip()


# -----------------------
# Downloader
# -----------------------

def download_with_retries(url: str, dst: Path, retries: int, timeout: int) -> None:
    last_err: Optional[Exception] = None
    headers = {"User-Agent": "animica-fetcher/1.0 (+https://animica.example)"}
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp_name = tmp.name
                    copied = 0
                    while True:
                        chunk = resp.read(1 << 16)
                        if not chunk:
                            break
                        tmp.write(chunk)
                        copied += len(chunk)
                    tmp.flush()
            # Move into place after full download.
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(tmp_name, dst)
            size_str = human_size(dst.stat().st_size)
            if total and total != copied:
                print(f"  ⚠ size mismatch: expected {human_size(total)}, got {size_str}")
            else:
                print(f"  ↓ saved {size_str}")
            return
        except (HTTPError, URLError, TimeoutError, OSError) as e:
            last_err = e
            print(f"  attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"Failed to download {url}: {last_err}")


def verify_checksum_if_any(path: Path, expected: Optional[str]) -> None:
    if not expected:
        return
    got = sha256_hex(path)
    if got.lower() != expected.lower():
        raise RuntimeError(
            f"Checksum mismatch for {path}:\n  expected {expected}\n  got      {got}"
        )


# -----------------------
# Main logic
# -----------------------

def assemble_url(base: str, a: Artifact, fi: FileItem) -> str:
    base = base.rstrip("/")
    return f"{base}/{a.system}/{a.circuit}/{fi.name}"


def ensure_artifact(
    a: Artifact,
    dest_root: Path,
    base_url: str,
    force: bool,
    retries: int,
    timeout: int,
) -> list[Path]:
    written: list[Path] = []
    for fi in a.files:
        local_dir = dest_root / a.system / a.circuit
        mkdirp(local_dir)
        out = local_dir / fi.name

        if out.exists() and not force:
            try:
                verify_checksum_if_any(out, fi.sha256)
                print(f"✔ {out} (already present)")
                continue
            except Exception as e:
                print(f"  checksum revalidate failed for {out}: {e}; will re-download")

        if not base_url:
            print(f"✖ No --base-url and ANIMICA_CIRCOM_BASE not set; cannot fetch {out.name}")
            continue

        url = assemble_url(base_url, a, fi)
        print(f"→ {a.system}/{a.circuit}/{fi.name}")
        print(f"  {url}")
        download_with_retries(url, out, retries=retries, timeout=timeout)
        verify_checksum_if_any(out, fi.sha256)
        written.append(out)
    return written


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch small Circom vk/proof fixtures for tests.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--dest",
        type=Path,
        default=Path("zk/circuits"),
        help="Destination circuits root (folders will be created as needed).",
    )
    p.add_argument(
        "--base-url",
        type=str,
        default=_default_base_url(),
        help="Base URL/mirror hosting the artifacts. Can also be set via ANIMICA_CIRCOM_BASE.",
    )
    p.add_argument(
        "--only",
        type=str,
        default=None,
        help='Filter to a specific circuit like "groth16/embedding" (substring match).',
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the file exists and passes checksum (if any).",
    )
    p.add_argument(
        "--retries", type=int, default=3, help="Network retries per file."
    )
    p.add_argument(
        "--timeout", type=int, default=20, help="Download timeout (seconds)."
    )
    return p.parse_args(list(argv))


def main(argv: Iterable[str]) -> int:
    args = parse_args(argv)

    # Friendly banner
    print("Animica: fetch Circom artifacts")
    print(f"  dest     : {args.dest}")
    print(f"  base-url : {args.base_url or '(not set)'}")
    if args.only:
        print(f"  filter   : {args.only}")
    print(f"  force    : {args.force}\n")

    any_written = False
    for a in ARTIFACTS:
        if not matches_filter(a, args.only):
            continue
        try:
            written = ensure_artifact(
                a=a,
                dest_root=args.dest,
                base_url=args.base_url,
                force=args.force,
                retries=args.retries,
                timeout=args.timeout,
            )
            any_written |= bool(written)
        except Exception as e:
            print(f"✖ failed for {a.system}/{a.circuit}: {e}", file=sys.stderr)
            return 2

    if not any_written:
        print("No files written (they may already be present, or base-url was not set).")
    else:
        print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

