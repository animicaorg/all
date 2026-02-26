from __future__ import annotations


def run_eval(step: int) -> dict[str, float]:
    return {
        "text_ppl": max(1.1, 50.0 / (step + 1)),
        "image_fid_lite": max(5.0, 100.0 / (step + 1)),
        "video_consistency": min(1.0, step / 200.0),
    }
