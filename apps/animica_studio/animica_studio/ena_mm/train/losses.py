from __future__ import annotations


def text_loss_proxy(step: int) -> float:
    return max(0.01, 2.0 / (step + 5))


def diffusion_loss_proxy(step: int) -> float:
    return max(0.02, 3.0 / (step + 8))
