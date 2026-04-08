"""Helpers for packaging Animica Studio release artifacts."""

from __future__ import annotations

import math
import platform
import re
import shutil
import subprocess
import textwrap
import tomllib
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = APP_ROOT / "animica_studio"
DIST_DIR = APP_ROOT / "dist"
BUILD_DIR = APP_ROOT / "build"

APP_DISPLAY_NAME = "Animica Studio"
APP_BUNDLE_NAME = "AnimicaStudio"
LINUX_PACKAGE_NAME = "animica-studio"
LINUX_INSTALL_DIR = Path("/opt") / LINUX_PACKAGE_NAME
LINUX_ICON_SOURCE = PACKAGE_ROOT / "ui" / "resources" / "icons" / "logo.svg"
PROJECT_FILE = APP_ROOT / "pyproject.toml"

DEB_RUNTIME_DEPENDS = [
    "libasound2 | libasound2t64",
    "libdbus-1-3",
    "libegl1",
    "libfontconfig1",
    "libglib2.0-0",
    "libgl1",
    "libnspr4",
    "libnss3",
    "libx11-6",
    "libx11-xcb1",
    "libxcb1",
    "libxcomposite1",
    "libxdamage1",
    "libxfixes3",
    "libxkbcommon0",
    "libxrandr2",
]


def read_project_version(pyproject_path: Path = PROJECT_FILE) -> str:
    """Return the declared project version from ``pyproject.toml``."""
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def read_version_file(version_path: Path = PACKAGE_ROOT / "_version.py") -> str:
    """Return the version written by ``scripts/build_version.py`` if present."""
    if not version_path.exists():
        return read_project_version()
    match = re.search(
        r'__version__\s*=\s*"(?P<version>[^"]+)"',
        version_path.read_text(encoding="utf-8"),
    )
    if not match:
        return read_project_version()
    return match.group("version")


def normalize_release_version(raw_version: str, fallback_version: str | None = None) -> str:
    """Normalize git-derived versions into artifact-safe release versions."""
    fallback = _sanitize_version_token(fallback_version or read_project_version(), keep_hyphen=True)
    fallback = fallback or "0.1.0"
    candidate = _sanitize_version_token(raw_version, keep_hyphen=True)
    if candidate and candidate[0].isdigit():
        return candidate
    local = _sanitize_version_token(raw_version, keep_hyphen=False)
    if not local:
        return fallback
    return f"{fallback}+{local}"


def expected_linux_bundle(dist_dir: Path = DIST_DIR) -> Path:
    return dist_dir / APP_BUNDLE_NAME


def expected_macos_app(dist_dir: Path = DIST_DIR) -> Path:
    return dist_dir / f"{APP_BUNDLE_NAME}.app"


def expected_windows_exe(dist_dir: Path = DIST_DIR) -> Path:
    return dist_dir / f"{APP_BUNDLE_NAME}.exe"


def build_linux_deb(
    version: str,
    source_dir: Path | None = None,
    dist_dir: Path = DIST_DIR,
    build_dir: Path = BUILD_DIR,
) -> Path:
    """Wrap the PyInstaller Linux bundle into a Debian package."""
    source_dir = source_dir or expected_linux_bundle(dist_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(
            f"Linux bundle not found at {source_dir}. Run the Linux PyInstaller build first."
        )
    if not shutil.which("dpkg-deb"):
        raise RuntimeError("dpkg-deb is required to build a .deb package.")

    architecture = detect_debian_architecture()
    artifact_path = dist_dir / f"{LINUX_PACKAGE_NAME}_{version}_{architecture}.deb"
    stage_root = build_dir / "deb" / f"{LINUX_PACKAGE_NAME}-{version}"
    payload_root = stage_root / "root"
    install_root = payload_root / LINUX_INSTALL_DIR.relative_to("/")

    if stage_root.exists():
        shutil.rmtree(stage_root)
    install_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, install_root, symlinks=True)

    _write_executable(
        payload_root / "usr" / "bin" / LINUX_PACKAGE_NAME,
        textwrap.dedent(
            f"""\
            #!/bin/sh
            APPDIR="{LINUX_INSTALL_DIR}"
            cd "$APPDIR"
            exec "$APPDIR/{APP_BUNDLE_NAME}" "$@"
            """
        ),
    )

    icon_target = payload_root / "usr" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    icon_target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LINUX_ICON_SOURCE, icon_target / f"{LINUX_PACKAGE_NAME}.svg")

    desktop_target = payload_root / "usr" / "share" / "applications" / f"{LINUX_PACKAGE_NAME}.desktop"
    desktop_target.parent.mkdir(parents=True, exist_ok=True)
    desktop_target.write_text(linux_desktop_entry(), encoding="utf-8")

    control_dir = payload_root / "DEBIAN"
    control_dir.mkdir(parents=True, exist_ok=True)
    installed_size = installed_size_kib(payload_root)
    control_dir.joinpath("control").write_text(
        deb_control_file(version=version, architecture=architecture, installed_size=installed_size),
        encoding="utf-8",
    )

    if artifact_path.exists():
        artifact_path.unlink()
    subprocess.run(["dpkg-deb", "--build", str(payload_root), str(artifact_path)], check=True)
    return artifact_path


def detect_debian_architecture() -> str:
    """Return Debian architecture for the current host."""
    try:
        return (
            subprocess.check_output(["dpkg", "--print-architecture"], text=True, stderr=subprocess.DEVNULL)
            .strip()
        )
    except Exception:
        machine = platform.machine().lower()
        return {
            "x86_64": "amd64",
            "amd64": "amd64",
            "aarch64": "arm64",
            "arm64": "arm64",
        }.get(machine, machine)


def installed_size_kib(root: Path) -> int:
    """Return installed size in KiB for Debian control metadata."""
    total_bytes = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "DEBIAN" in path.parts:
            continue
        total_bytes += path.stat().st_size
    return max(1, math.ceil(total_bytes / 1024))


def linux_desktop_entry() -> str:
    """Return the desktop entry for the Debian package."""
    return textwrap.dedent(
        f"""\
        [Desktop Entry]
        Type=Application
        Name={APP_DISPLAY_NAME}
        Comment=Desktop application for the Animica blockchain
        Exec={LINUX_PACKAGE_NAME} %U
        TryExec={LINUX_PACKAGE_NAME}
        Icon={LINUX_PACKAGE_NAME}
        Terminal=false
        Categories=Development;Utility;
        StartupNotify=true
        StartupWMClass={APP_BUNDLE_NAME}
        """
    )


def deb_control_file(version: str, architecture: str, installed_size: int) -> str:
    """Return Debian control metadata for the packaged Studio build."""
    depends = ", ".join(DEB_RUNTIME_DEPENDS)
    return textwrap.dedent(
        f"""\
        Package: {LINUX_PACKAGE_NAME}
        Version: {version}
        Section: devel
        Priority: optional
        Architecture: {architecture}
        Depends: {depends}
        Installed-Size: {installed_size}
        Maintainer: Animica Team <support@animica.org>
        Description: Animica Studio desktop application
         Desktop workstation for building, testing, and operating against the
         Animica blockchain from a bundled Qt runtime.
        """
    )


def _sanitize_version_token(value: str, *, keep_hyphen: bool) -> str:
    token = value.strip()
    if token.startswith("v") and len(token) > 1 and token[1].isdigit():
        token = token[1:]
    token = token.replace("_", ".")
    pattern = r"[^0-9A-Za-z.+~-]+" if keep_hyphen else r"[^0-9A-Za-z.+~]+"
    token = re.sub(pattern, ".", token)
    if not keep_hyphen:
        token = token.replace("-", ".")
    token = re.sub(r"\.{2,}", ".", token)
    return token.strip(".-+~")


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
