"""Build wheel-bundled Docker images on demand for pip-only installs.

Background:
    Source-tree installs build the heavy `node.Dockerfile` / `miner.Dockerfile`
    from the repo at compose time (`build:` directive). pip-only installs
    cannot do that because the source tree is not present — they use the
    bundled standalone compose files (`_data/ops/docker/standalone/*.yml`),
    which reference images by tag (e.g. `animica-local/node:0.1.5`).

    Those tags are NOT published on Docker Hub. Instead, this module builds
    them locally from a slim `Dockerfile.node` / `Dockerfile.miner` shipped
    in the wheel under `_data/ops/docker/standalone/`. Each Dockerfile
    `pip install animica==<version>`s the same wheel the host already has,
    so the image and host code stay version-aligned.

The cost: a one-time ~30-60 second build on first `animica node up`. After
that, the image is cached and subsequent boots reuse it. Subsequent wheel
upgrades trigger a rebuild because the image tag carries the version.

Override behavior:
    - `NODE_IMAGE` / `MINER_IMAGE` env vars are passed through to compose
      unchanged. Setting them to a published registry image bypasses the
      local build entirely.
    - `ANIMICA_SKIP_IMAGE_BUILD=1` returns without building (useful when
      the user has pre-built or pre-pulled the images themselves).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

NODE_DEFAULT_IMAGE_TEMPLATE = "animica-local/node:{version}"
MINER_DEFAULT_IMAGE_TEMPLATE = "animica-local/miner:{version}"


def get_animica_version() -> str:
    """Resolve the installed `animica` wheel version.

    Falls back to "latest" if the package metadata is unavailable (running
    from a source checkout without an installed wheel). Compose still works
    in that case — the tag just gets a `:latest` suffix.
    """
    try:
        return pkg_version("animica")
    except PackageNotFoundError:
        return "latest"


def default_node_image() -> str:
    return NODE_DEFAULT_IMAGE_TEMPLATE.format(version=get_animica_version())


def default_miner_image() -> str:
    return MINER_DEFAULT_IMAGE_TEMPLATE.format(version=get_animica_version())


def _docker_bin() -> Optional[str]:
    return shutil.which("docker")


def _image_exists_locally(docker: str, image: str) -> bool:
    """Return True when `docker images -q <image>` prints a non-empty id."""
    try:
        proc = subprocess.run(
            [docker, "images", "-q", image],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("docker images query failed for %s: %s", image, exc)
        return False
    return bool(proc.stdout.strip())


def _resolve_standalone_dir() -> Path:
    """Locate the directory holding Dockerfile.node / Dockerfile.miner.

    Resolution order:
      1. Wheel-bundled `animica/_data/ops/docker/standalone/`.
      2. Source tree `ops/docker/standalone/` (when running from a checkout).
    """
    pkg_dir = Path(__file__).resolve().parent
    wheel_path = pkg_dir / "_data" / "ops" / "docker" / "standalone"
    if (wheel_path / "Dockerfile.node").exists():
        return wheel_path

    repo_root_env = os.environ.get("ANIMICA_REPO_ROOT")
    repo_root = (
        Path(repo_root_env).expanduser().resolve()
        if repo_root_env
        else pkg_dir.parents[1]
    )
    source_path = repo_root / "ops" / "docker" / "standalone"
    if (source_path / "Dockerfile.node").exists():
        return source_path

    raise FileNotFoundError(
        "Could not locate standalone Dockerfiles. Looked in "
        f"{wheel_path} and {source_path}."
    )


def _build_image(
    docker: str,
    dockerfile: Path,
    image: str,
    version: str,
    *,
    quiet: bool,
) -> None:
    cmd = [
        docker,
        "build",
        "--pull",
        "--build-arg",
        f"ANIMICA_VERSION={version}",
        "-t",
        image,
        "-f",
        str(dockerfile),
        str(dockerfile.parent),
    ]
    if quiet:
        cmd.insert(2, "--quiet")

    logger.info("Building %s from %s …", image, dockerfile.name)
    subprocess.run(cmd, check=True)


def ensure_standalone_images(
    *,
    include_miner: bool = False,
    quiet: bool = False,
) -> dict[str, str]:
    """Build the standalone node/miner images if they are not already present.

    Returns a dict of image tags that the caller can stuff into the compose
    environment so the values stay consistent if the version changes mid-run.

    The function is a no-op when:
      - $ANIMICA_SKIP_IMAGE_BUILD=1
      - $NODE_IMAGE / $MINER_IMAGE point at custom images (we just trust them
        to exist; compose will surface any pull failures)
      - Docker is not on PATH (we let compose surface the missing-docker
        error its own way).
    """
    if os.environ.get("ANIMICA_SKIP_IMAGE_BUILD") == "1":
        return {}

    docker = _docker_bin()
    if not docker:
        logger.debug("docker not on PATH — skipping image pre-build")
        return {}

    version = get_animica_version()
    standalone_dir = _resolve_standalone_dir()
    out: dict[str, str] = {"ANIMICA_VERSION": version}

    node_image = os.environ.get("NODE_IMAGE") or default_node_image()
    if not os.environ.get("NODE_IMAGE") and not _image_exists_locally(
        docker, node_image
    ):
        _build_image(
            docker,
            standalone_dir / "Dockerfile.node",
            node_image,
            version,
            quiet=quiet,
        )
    out["NODE_IMAGE"] = node_image

    if include_miner:
        miner_image = os.environ.get("MINER_IMAGE") or default_miner_image()
        if not os.environ.get("MINER_IMAGE") and not _image_exists_locally(
            docker, miner_image
        ):
            _build_image(
                docker,
                standalone_dir / "Dockerfile.miner",
                miner_image,
                version,
                quiet=quiet,
            )
        out["MINER_IMAGE"] = miner_image

    return out


__all__ = [
    "default_miner_image",
    "default_node_image",
    "ensure_standalone_images",
    "get_animica_version",
]
