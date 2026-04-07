from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from .config import load_config_from_env
from .portal import (DEFAULT_VERSION, PLACEHOLDER_ADDRESS, BundleInput,
                     ResolvedMiningConfig, build_bundle_input,
                     build_bundle_readme, build_config_document,
                     build_launcher_script, resolve_public_mining_config)


@dataclass(frozen=True)
class BundleArtifact:
    platform: str
    filename: str
    path: Path
    media_type: str
    sha256: str
    size_bytes: int
    version: str
    launcher: str
    personalized: bool


class MinerBundleBuilder:
    def __init__(self, output_dir: Path | None = None, *, version: str = DEFAULT_VERSION) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        self._output_dir = output_dir or Path(
            os.getenv("ANIMICA_MINING_DOWNLOAD_DIR", str(repo_root / "artifacts" / "miners"))
        )
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._version = version
        self._source_path = Path(__file__).with_name("reference_cpu_miner.py")

    def build(self, resolved: ResolvedMiningConfig, platform: str, bundle: BundleInput) -> BundleArtifact:
        platform = platform.lower()
        if platform not in {"windows", "macos", "linux"}:
            raise ValueError(f"unsupported platform: {platform}")

        ext = "zip" if platform in {"windows", "macos"} else "tar.gz"
        media_type = "application/zip" if ext == "zip" else "application/gzip"
        launcher = {
            "windows": "start_mining.bat",
            "macos": "start_mining.command",
            "linux": "start_mining.sh",
        }[platform]
        personalized = bundle.address != PLACEHOLDER_ADDRESS

        key_payload = {
            "platform": platform,
            "version": self._version,
            "endpoint": resolved.stratum_url,
            "network": resolved.network,
            "profile": resolved.profile,
            "address": bundle.address,
            "worker": bundle.worker,
            "threads": bundle.threads,
        }
        digest = hashlib.sha256(json.dumps(key_payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        stem = f"animica-cpu-miner-{self._version}-{resolved.network}-{platform}"
        archive_name = f"{stem}-{digest}.{ext}"
        archive_path = self._output_dir / archive_name

        if not archive_path.exists():
            self._build_archive(
                archive_path=archive_path,
                platform=platform,
                launcher=launcher,
                resolved=resolved,
                bundle=bundle,
                ext=ext,
            )

        sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        return BundleArtifact(
            platform=platform,
            filename=f"{stem}.{ext}",
            path=archive_path,
            media_type=media_type,
            sha256=sha256,
            size_bytes=archive_path.stat().st_size,
            version=self._version,
            launcher=launcher,
            personalized=personalized,
        )

    def _build_archive(
        self,
        *,
        archive_path: Path,
        platform: str,
        launcher: str,
        resolved: ResolvedMiningConfig,
        bundle: BundleInput,
        ext: str,
    ) -> None:
        config_name = "animica-miner.config.json"
        with tempfile.TemporaryDirectory(dir=self._output_dir) as tmp_dir:
            staging_root = Path(tmp_dir) / "bundle"
            staging_root.mkdir(parents=True, exist_ok=True)

            miner_target = staging_root / "animica_cpu_miner.py"
            miner_target.write_text(self._source_path.read_text(encoding="utf-8"), encoding="utf-8")
            config_target = staging_root / config_name
            config_target.write_text(build_config_document(resolved, bundle), encoding="utf-8")
            launcher_target = staging_root / launcher
            launcher_target.write_text(
                build_launcher_script(platform, resolved, config_name=config_name),
                encoding="utf-8",
                newline="\r\n" if platform == "windows" else "\n",
            )
            readme_target = staging_root / "README.md"
            readme_target.write_text(
                build_bundle_readme(resolved, bundle, version=self._version),
                encoding="utf-8",
            )

            for file_path in (miner_target, launcher_target):
                file_path.chmod(0o755)

            if ext == "zip":
                self._write_zip(archive_path, staging_root)
            else:
                self._write_tar(archive_path, staging_root)

    def _write_zip(self, archive_path: Path, staging_root: Path) -> None:
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as zf:
            for file_path in sorted(staging_root.iterdir()):
                executable = file_path.name in {"animica_cpu_miner.py", "start_mining.command"}
                info = ZipInfo(file_path.name)
                info.compress_type = ZIP_DEFLATED
                info.external_attr = (0o755 if executable else 0o644) << 16
                zf.writestr(info, file_path.read_bytes())

    def _write_tar(self, archive_path: Path, staging_root: Path) -> None:
        with tarfile.open(archive_path, "w:gz") as tf:
            for file_path in sorted(staging_root.iterdir()):
                tf.add(file_path, arcname=file_path.name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Animica mining starter bundles")
    parser.add_argument("--output-dir")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--scheme")
    parser.add_argument("--address")
    parser.add_argument("--worker")
    parser.add_argument("--threads", type=int)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config_from_env()
    resolved = resolve_public_mining_config(config, request=None)
    if args.host:
        resolved = replace(resolved, public_host=args.host)
    if args.port:
        resolved = replace(resolved, public_port=args.port)
    if args.scheme:
        resolved = replace(resolved, public_scheme=args.scheme)
    bundle = build_bundle_input(address=args.address, worker=args.worker, threads=args.threads)
    builder = MinerBundleBuilder(
        Path(args.output_dir) if args.output_dir else None,
        version=args.version,
    )
    manifest = {"endpoint": resolved.stratum_url, "bundles": []}
    for platform in ("windows", "macos", "linux"):
        artifact = builder.build(resolved, platform, bundle)
        manifest["bundles"].append(
            {
                "platform": artifact.platform,
                "filename": artifact.filename,
                "path": str(artifact.path),
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "launcher": artifact.launcher,
                "personalized": artifact.personalized,
                "version": artifact.version,
            }
        )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
