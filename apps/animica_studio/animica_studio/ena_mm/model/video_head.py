from __future__ import annotations

import torch
from torch import nn


class VideoHead(nn.Module):
    def __init__(self, hidden_size: int = 128, latent: int = 64) -> None:
        super().__init__()
        self.temporal = nn.GRU(input_size=hidden_size, hidden_size=hidden_size, batch_first=True)
        self.proj = nn.Linear(hidden_size, latent)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.temporal(x)
        return self.proj(out)
