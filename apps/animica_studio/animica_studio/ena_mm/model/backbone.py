from __future__ import annotations

import torch
from torch import nn


class SharedBackbone(nn.Module):
    def __init__(self, hidden_size: int = 128) -> None:
        super().__init__()
        self.net = nn.Sequential(nn.Linear(hidden_size, hidden_size), nn.ReLU(), nn.Linear(hidden_size, hidden_size))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)
