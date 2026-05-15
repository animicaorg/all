#!/usr/bin/env python3
"""Stage: continual pre-training (CPT).

Inputs:  runs/<run_id>/datasets/cpt.jsonl + ai/configs/training.yaml
Outputs: runs/<run_id>/training/cpt/{backend.json,summary.json,checkpoints/}
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"
                       / "agent_runtime" / "src"))

from agent_runtime.config import load_config
from flagship_agent.training import (
    full_stage, lite_stage, resolve_backend, simulate_stage,
)


def main() -> int:
    cfg = load_config()
    if not cfg.training["cpt"].get("enabled", True):
        print("[train_cpt] disabled by config")
        return 0
    run_id = os.environ["FLAGSHIP_RUN_ID"]
    pkg = Path(os.environ["FLAGSHIP_PKG_DIR"])
    dataset = pkg / "runs" / run_id / "datasets" / "cpt.jsonl"
    out_dir = pkg / "runs" / run_id / "training" / "cpt"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not dataset.is_file():
        print(f"[train_cpt] missing {dataset}", file=sys.stderr)
        return 1

    backend = resolve_backend(
        requested=cfg.mode,
        requested_base_model=cfg.training["base_model"]["id"],
        training_cfg=cfg.training,
    )
    stage_cfg = dict(cfg.training["cpt"])
    if cfg.mode == "simulate":
        simulate_stage(kind="cpt", dataset_path=dataset, out_dir=out_dir,
                        backend=backend, stage_cfg=stage_cfg)
    elif cfg.mode == "lite":
        lite_stage(kind="cpt", dataset_path=dataset, out_dir=out_dir,
                    backend=backend, stage_cfg=stage_cfg)
    else:
        full_stage(kind="cpt", dataset_path=dataset, out_dir=out_dir,
                    backend=backend, stage_cfg=stage_cfg)
    print(f"[train_cpt] mode={cfg.mode} effective={backend.effective_mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
