#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path


WINDOWS_INSTALLER_FILENAME = "animica-wallet-windows-x64.exe"
WINDOWS_ZIP_FILENAME = "animica-wallet-windows-x64.zip"
WINDOWS_CHECKSUM_FILENAME = "animica-wallet-windows.sha256"

MACOS_DMG_FILENAME = "animicawallet.dmg"
MACOS_ZIP_FILENAME = "animicawalletmac.zip"
MACOS_CHECKSUM_FILENAME = "animicawallet.sha256"

LINUX_APPIMAGE_FILENAME = "animica-wallet-linux.AppImage"
LINUX_DEB_FILENAME = "animica-wallet-linux.deb"
LINUX_TARBALL_FILENAME = "animica-wallet-linux.tar.gz"
LINUX_RAW_FILENAME = "animica-wallet"
LINUX_PACKAGE_CHECKSUM_FILENAME = "animica-wallet-linux.sha256"
LINUX_RAW_CHECKSUM_FILENAME = "animica-wallet.sha256"


class ManifestError(RuntimeError):
    pass


DEFAULT_PLATFORM_ARCHITECTURES = {
    "windows": "x86_64",
    "macos": "universal",
    "linux": "x86_64",
}


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksum_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    checksums: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ManifestError(f"Malformed checksum line {line_number} in {path}")
        checksum, filename = parts
        checksums[filename.strip()] = checksum.strip().lower()
    return checksums


def artifact_entry(website_dir: Path, filename: str) -> dict[str, object] | None:
    path = website_dir / filename
    if not path.exists():
        return None

    return {
        "url": f"/wallet/{filename}",
        "filename": filename,
        "sha256": compute_sha256(path),
        "size_bytes": path.stat().st_size,
    }


def load_existing_manifest(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Failed to parse existing manifest {path}: {exc}") from exc

    return data if isinstance(data, dict) else None


def resolve_platform_architecture(
    platform: str,
    explicit_architecture: str | None,
    existing_manifest: dict[str, object] | None,
    fallback_architecture: str,
) -> str:
    if explicit_architecture:
        return explicit_architecture

    if existing_manifest:
        section = existing_manifest.get(platform)
        if isinstance(section, dict):
            existing_architecture = section.get("architecture")
            if isinstance(existing_architecture, str) and existing_architecture.strip():
                return existing_architecture.strip()

    if fallback_architecture:
        return fallback_architecture

    return DEFAULT_PLATFORM_ARCHITECTURES[platform]


def validate_checksum_entries(
    website_dir: Path,
    checksum_filename: str,
    expected_filenames: list[str],
) -> dict[str, object] | None:
    checksum_path = website_dir / checksum_filename
    if not expected_filenames and not checksum_path.exists():
        return None

    if expected_filenames and not checksum_path.exists():
        raise ManifestError(f"Expected checksum file {checksum_path} for published artifacts")

    parsed = parse_checksum_file(checksum_path)
    for filename in expected_filenames:
        actual_path = website_dir / filename
        if filename not in parsed:
            raise ManifestError(f"{checksum_path} does not contain a checksum for {filename}")
        actual_checksum = compute_sha256(actual_path)
        if parsed[filename] != actual_checksum:
            raise ManifestError(
                f"Checksum mismatch for {filename}: manifest has {parsed[filename]}, actual is {actual_checksum}"
            )

    return {
        "url": f"/wallet/{checksum_filename}",
        "filename": checksum_filename,
        "size_bytes": checksum_path.stat().st_size,
    }


def build_windows_section(website_dir: Path, version: str, architecture: str) -> dict[str, object] | None:
    installer = artifact_entry(website_dir, WINDOWS_INSTALLER_FILENAME)
    portable_zip = artifact_entry(website_dir, WINDOWS_ZIP_FILENAME)
    if installer is None and portable_zip is None:
        return None

    checksum = validate_checksum_entries(
        website_dir,
        WINDOWS_CHECKSUM_FILENAME,
        [entry["filename"] for entry in (installer, portable_zip) if entry is not None],
    )

    section: dict[str, object] = {
        "architecture": architecture,
        "build_label": version,
    }
    if installer is not None:
        section.update(
            {
                "installer_url": installer["url"],
                "installer_filename": installer["filename"],
                "installer_sha256": installer["sha256"],
                "installer_size_bytes": installer["size_bytes"],
            }
        )
    if portable_zip is not None:
        section.update(
            {
                "zip_url": portable_zip["url"],
                "zip_filename": portable_zip["filename"],
                "zip_sha256": portable_zip["sha256"],
                "zip_size_bytes": portable_zip["size_bytes"],
            }
        )
    if checksum is not None:
        section.update(
            {
                "checksum_url": checksum["url"],
                "checksum_filename": checksum["filename"],
                "checksum_size_bytes": checksum["size_bytes"],
            }
        )
    return section


def build_macos_section(website_dir: Path, version: str, architecture: str) -> dict[str, object] | None:
    installer = artifact_entry(website_dir, MACOS_DMG_FILENAME)
    portable_zip = artifact_entry(website_dir, MACOS_ZIP_FILENAME)
    if installer is None and portable_zip is None:
        return None

    checksum = validate_checksum_entries(
        website_dir,
        MACOS_CHECKSUM_FILENAME,
        [entry["filename"] for entry in (installer, portable_zip) if entry is not None],
    )

    section: dict[str, object] = {
        "architecture": architecture,
        "build_label": version,
    }
    if installer is not None:
        section.update(
            {
                "installer_url": installer["url"],
                "installer_filename": installer["filename"],
                "installer_sha256": installer["sha256"],
                "installer_size_bytes": installer["size_bytes"],
            }
        )
    if portable_zip is not None:
        section.update(
            {
                "zip_url": portable_zip["url"],
                "zip_filename": portable_zip["filename"],
                "zip_sha256": portable_zip["sha256"],
                "zip_size_bytes": portable_zip["size_bytes"],
            }
        )
    if checksum is not None:
        section.update(
            {
                "checksum_url": checksum["url"],
                "checksum_filename": checksum["filename"],
                "checksum_size_bytes": checksum["size_bytes"],
            }
        )
    return section


def build_linux_section(website_dir: Path, version: str, architecture: str) -> dict[str, object] | None:
    appimage = artifact_entry(website_dir, LINUX_APPIMAGE_FILENAME)
    deb = artifact_entry(website_dir, LINUX_DEB_FILENAME)
    tarball = artifact_entry(website_dir, LINUX_TARBALL_FILENAME)
    raw = artifact_entry(website_dir, LINUX_RAW_FILENAME)
    if appimage is None and deb is None and tarball is None and raw is None:
        return None

    package_files = [entry["filename"] for entry in (appimage, deb, tarball) if entry is not None]
    package_checksum = validate_checksum_entries(
        website_dir,
        LINUX_PACKAGE_CHECKSUM_FILENAME,
        package_files,
    )
    raw_checksum = validate_checksum_entries(
        website_dir,
        LINUX_RAW_CHECKSUM_FILENAME,
        [raw["filename"]] if raw is not None else [],
    )

    section: dict[str, object] = {
        "architecture": architecture,
        "build_label": version,
    }
    if appimage is not None:
        section.update(
            {
                "appimage_url": appimage["url"],
                "appimage_filename": appimage["filename"],
                "appimage_sha256": appimage["sha256"],
                "appimage_size_bytes": appimage["size_bytes"],
            }
        )
    if deb is not None:
        section.update(
            {
                "deb_url": deb["url"],
                "deb_filename": deb["filename"],
                "deb_sha256": deb["sha256"],
                "deb_size_bytes": deb["size_bytes"],
            }
        )
    if tarball is not None:
        section.update(
            {
                "tarball_url": tarball["url"],
                "tarball_filename": tarball["filename"],
                "tarball_sha256": tarball["sha256"],
                "tarball_size_bytes": tarball["size_bytes"],
            }
        )
    if raw is not None:
        section.update(
            {
                "raw_url": raw["url"],
                "raw_filename": raw["filename"],
                "raw_sha256": raw["sha256"],
                "raw_size_bytes": raw["size_bytes"],
            }
        )
    if package_checksum is not None:
        section.update(
            {
                "package_checksum_url": package_checksum["url"],
                "package_checksum_filename": package_checksum["filename"],
                "package_checksum_size_bytes": package_checksum["size_bytes"],
            }
        )
    if raw_checksum is not None:
        section.update(
            {
                "raw_checksum_url": raw_checksum["url"],
                "raw_checksum_filename": raw_checksum["filename"],
                "raw_checksum_size_bytes": raw_checksum["size_bytes"],
            }
        )
    return section


def build_manifest(
    website_dir: Path,
    version: str,
    generated_at: str,
    architecture: str,
    *,
    existing_manifest: dict[str, object] | None = None,
    windows_architecture: str | None = None,
    macos_architecture: str | None = None,
    linux_architecture: str | None = None,
) -> dict[str, object]:
    manifest: dict[str, object] = {
        "version": version,
        "generated_at": generated_at,
    }

    windows = build_windows_section(
        website_dir,
        version,
        resolve_platform_architecture("windows", windows_architecture, existing_manifest, architecture),
    )
    macos = build_macos_section(
        website_dir,
        version,
        resolve_platform_architecture("macos", macos_architecture, existing_manifest, architecture),
    )
    linux = build_linux_section(
        website_dir,
        version,
        resolve_platform_architecture("linux", linux_architecture, existing_manifest, architecture),
    )

    if windows is not None:
        manifest["windows"] = windows
    if macos is not None:
        manifest["macos"] = macos
    if linux is not None:
        manifest["linux"] = linux
    if "windows" not in manifest and "macos" not in manifest and "linux" not in manifest:
        raise ManifestError(f"No wallet artifacts found under {website_dir}")

    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate website/public/wallet/manifest.json")
    parser.add_argument("--website-dir", required=True, help="Path to website/public/wallet")
    parser.add_argument("--version", required=True, help="Version/build label to expose on the website")
    parser.add_argument(
        "--output",
        default="manifest.json",
        help="Output filename inside the website wallet directory (default: manifest.json)",
    )
    parser.add_argument(
        "--generated-at",
        default=dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        help="UTC timestamp for the manifest (default: current time)",
    )
    parser.add_argument(
        "--architecture",
        default="x86_64",
        help="Architecture label to attach to published artifacts (default: x86_64)",
    )
    parser.add_argument("--windows-architecture", help="Windows architecture label override")
    parser.add_argument("--macos-architecture", help="macOS architecture label override")
    parser.add_argument("--linux-architecture", help="Linux architecture label override")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    website_dir = Path(args.website_dir).expanduser().resolve()
    output_path = website_dir / args.output

    try:
        manifest = build_manifest(
            website_dir,
            args.version,
            args.generated_at,
            args.architecture,
            existing_manifest=load_existing_manifest(output_path),
            windows_architecture=args.windows_architecture,
            macos_architecture=args.macos_architecture,
            linux_architecture=args.linux_architecture,
        )
    except ManifestError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    output_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] Wrote wallet manifest to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
