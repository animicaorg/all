#!/usr/bin/env python3
"""Stage: export a downloadable bundle of weights + tokenizer + manifest.

Produces models/export/<run_id>/{manifest.json,inference.json,model/...}.
When ai/configs/integration.yaml::ipfs.publish_to_ipfs is true and an IPFS
daemon is reachable, also uploads the bundle and writes the resulting CID
to manifest.json.

Also (re)points models/export/latest at this run.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"
                       / "agent_runtime" / "src"))
from agent_runtime.config import load_config

_RUN_ID = os.environ["FLAGSHIP_RUN_ID"]
_PKG_DIR = Path(os.environ["FLAGSHIP_PKG_DIR"])
_RUN_DIR = _PKG_DIR / "runs" / _RUN_ID
_EXPORT_ROOT = _PKG_DIR / "models" / "export"
_OUT_DIR = _EXPORT_ROOT / _RUN_ID
_OUT_DIR.mkdir(parents=True, exist_ok=True)


def _sha256_dir(path: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(path.rglob("*")):
        if p.is_file():
            rel = p.relative_to(path).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            with p.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
    return h.hexdigest()


def _tar_zst(src: Path, dst: Path) -> bool:
    """Tar+zstd compress; falls back to gzip if zstd isn't available."""
    if shutil.which("zstd"):
        try:
            with tarfile.open(dst.with_suffix(".tar"), "w") as tf:
                tf.add(src, arcname=src.name)
            subprocess.check_call(["zstd", "-q", "--rm", "-19",
                                    str(dst.with_suffix(".tar")),
                                    "-o", str(dst)])
            return True
        except (OSError, subprocess.CalledProcessError):
            pass
    # gz fallback
    with tarfile.open(dst.with_suffix(".tar.gz"), "w:gz") as tf:
        tf.add(src, arcname=src.name)
    return False


def _publish_ipfs(bundle_tar: Path, cfg) -> Optional[str]:
    if not cfg.integration["ipfs"].get("publish_to_ipfs", False):
        return None
    api = cfg.integration["ipfs"].get("api_endpoint")
    if not api:
        return None
    try:
        import httpx
        with bundle_tar.open("rb") as fh:
            r = httpx.post(f"{api}/api/v0/add?cid-version=1",
                           files={"file": (bundle_tar.name, fh)},
                           timeout=120.0)
        r.raise_for_status()
        cid = r.text.strip().splitlines()[-1]
        try:
            return json.loads(cid)["Hash"]
        except (json.JSONDecodeError, KeyError):
            return cid
    except Exception as exc:    # noqa: BLE001 — IPFS is best-effort
        print(f"[export] IPFS publish failed: {exc}", file=sys.stderr)
        return None


def main() -> int:
    cfg = load_config()
    sft_summary_path = _RUN_DIR / "training" / "sft" / "summary.json"
    sft_backend_path = _RUN_DIR / "training" / "sft" / "backend.json"
    eval_results_path = _RUN_DIR / "eval" / "results.json"
    if not sft_summary_path.is_file():
        print(f"[export] missing {sft_summary_path}", file=sys.stderr)
        return 1
    sft = json.loads(sft_summary_path.read_text(encoding="utf-8"))
    backend = (json.loads(sft_backend_path.read_text(encoding="utf-8"))
                if sft_backend_path.is_file() else {})
    evalr = (json.loads(eval_results_path.read_text(encoding="utf-8"))
              if eval_results_path.is_file() else {})

    # Copy the SFT checkpoint into the bundle as the "model".
    model_dir = _OUT_DIR / "model"
    if model_dir.is_dir():
        shutil.rmtree(model_dir)
    ckpt_path = Path(sft["checkpoint_dir"])
    if ckpt_path.is_dir():
        shutil.copytree(ckpt_path, model_dir)
    else:
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "MISSING_CHECKPOINT.txt").write_text(
            f"sft summary referenced {ckpt_path} but it does not exist",
            encoding="utf-8",
        )

    # Tier resolution from training run (multi-tier runs handled by
    # caller setting FLAGSHIP_TIER); for single-model runs we record the
    # base model id directly.
    tier = os.environ.get("FLAGSHIP_TIER", "flagship")
    base_model = backend.get("loaded_base_model") or backend.get(
        "requested_base_model") or "unknown"

    bundle_sha = _sha256_dir(_OUT_DIR)
    tarball = _OUT_DIR.with_suffix(".tar.zst")
    used_zstd = _tar_zst(_OUT_DIR, tarball)
    if not used_zstd:
        tarball = _OUT_DIR.with_suffix(".tar.gz")
    cid = _publish_ipfs(tarball, cfg) if tarball.is_file() else None

    manifest = {
        "schema": 1,
        "run_id": _RUN_ID,
        "tier": tier,
        "base_model": base_model,
        "requested_base_model": backend.get("requested_base_model", ""),
        "effective_mode": backend.get("effective_mode", ""),
        "available_for_real_inference":
            bool(evalr.get("available_for_real_inference", False)),
        "score": float(evalr.get("score", 0.0)),
        "verdict": evalr.get("verdict", "unknown"),
        "fallback_reasons": backend.get("fallback_reasons", []),
        "bundle_sha256": bundle_sha,
        "tarball": str(tarball.relative_to(_PKG_DIR)) if tarball.is_file()
                    else None,
        "ipfs_cid": cid,
        "exported_at": int(time.time()),
        "artifacts": {
            "model_dir": str(model_dir.relative_to(_OUT_DIR.parent.parent)),
            "manifest_json": "manifest.json",
            "inference_json": "inference.json",
        },
    }
    (_OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    inference_spec = {
        "schema": 1,
        "tier": tier,
        "base_model": base_model,
        "model_subdir": "model",
        "precision": backend.get("precision", "fp32"),
        "trust_remote_code": True,
        "context_window": 4096,
        "max_output_tokens_default": 1024,
    }
    (_OUT_DIR / "inference.json").write_text(
        json.dumps(inference_spec, indent=2), encoding="utf-8")
    # Model card
    (_OUT_DIR / "MODEL_CARD.md").write_text(_model_card(manifest),
                                              encoding="utf-8")
    # Compact summary for run_benchmark.py to read on the next run.
    (_OUT_DIR / "eval_summary.json").write_text(
        json.dumps({"score": manifest["score"],
                     "verdict": manifest["verdict"]},
                    indent=2), encoding="utf-8")

    # Repoint "latest" symlink.
    latest = _EXPORT_ROOT / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            if latest.is_symlink():
                latest.unlink()
            elif latest.is_dir():
                shutil.rmtree(latest)
        os.symlink(_RUN_ID, latest)
    except OSError as exc:
        print(f"[export] latest symlink update failed: {exc}", file=sys.stderr)

    print(f"[export] bundle={_OUT_DIR}  sha={bundle_sha[:12]}  "
          f"ipfs_cid={cid}  tier={tier}")
    return 0


def _model_card(m: dict) -> str:
    return f"""# Animica Flagship Coding Model — {m['tier']}

Run id: `{m['run_id']}`
Base model: `{m['base_model']}`
Effective mode: `{m['effective_mode']}`
Score: {m['score']}
Verdict: {m['verdict']}
Available for real inference: {m['available_for_real_inference']}

## Honesty

- requested_base_model: `{m['requested_base_model']}`
- fallback_reasons: {m['fallback_reasons']}
- bundle_sha256: `{m['bundle_sha256']}`
- ipfs_cid: `{m['ipfs_cid']}`

## Intended use

This bundle is intended for serving AICF inference jobs on the Animica
network. Miners pull this bundle by tier and run it as their flagship
worker model. Users access it via `animica chat`.

This bundle is **not** intended for unrestricted general-purpose use; it
is fine-tuned on the Animica monorepo and biased toward Animica
operational tasks.
"""


if __name__ == "__main__":
    raise SystemExit(main())
