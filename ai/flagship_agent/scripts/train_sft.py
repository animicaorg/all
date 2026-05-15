#!/usr/bin/env python3
"""Stage: supervised fine-tuning (SFT).

Inputs:  runs/<run_id>/datasets/sft.jsonl + cpt checkpoints + training.yaml
Outputs: runs/<run_id>/training/sft/{backend.json,summary.json,checkpoints/}
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
    if not cfg.training["sft"].get("enabled", True):
        print("[train_sft] disabled by config")
        return 0
    run_id = os.environ["FLAGSHIP_RUN_ID"]
    pkg = Path(os.environ["FLAGSHIP_PKG_DIR"])
    dataset = pkg / "runs" / run_id / "datasets" / "sft.jsonl"
    out_dir = pkg / "runs" / run_id / "training" / "sft"
    cpt_ckpt = pkg / "runs" / run_id / "training" / "cpt" / "checkpoints"
    cpt_latest = cpt_ckpt / "latest"
    if not cpt_latest.exists():
        # Pick the highest-numbered step folder.
        steps = sorted(cpt_ckpt.glob("step-*"))
        cpt_latest = steps[-1] if steps else None    # type: ignore
    out_dir.mkdir(parents=True, exist_ok=True)
    if not dataset.is_file():
        print(f"[train_sft] missing {dataset}", file=sys.stderr)
        return 1

    backend = resolve_backend(
        requested=cfg.mode,
        requested_base_model=cfg.training["base_model"]["id"],
        training_cfg=cfg.training,
    )
    stage_cfg = dict(cfg.training["sft"])
    if cfg.mode == "simulate":
        simulate_stage(kind="sft", dataset_path=dataset, out_dir=out_dir,
                        backend=backend, stage_cfg=stage_cfg)
    elif cfg.mode == "lite":
        lite_stage(kind="sft", dataset_path=dataset, out_dir=out_dir,
                    backend=backend, stage_cfg=stage_cfg)
    else:
        full_stage(kind="sft", dataset_path=dataset, out_dir=out_dir,
                    backend=backend, stage_cfg=stage_cfg,
                    base_checkpoint=cpt_latest if cpt_latest and
                                    cpt_latest.exists() else None)
    print(f"[train_sft] mode={cfg.mode} effective={backend.effective_mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
