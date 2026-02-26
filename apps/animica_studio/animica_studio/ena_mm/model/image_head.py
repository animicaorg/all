from __future__ import annotations

import torch
from torch import nn


class ImageHead(nn.Module):
    def __init__(self, hidden_size: int = 128, latent: int = 64) -> None:
        super().__init__()
        self.proj = nn.Linear(hidden_size, latent)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x)
