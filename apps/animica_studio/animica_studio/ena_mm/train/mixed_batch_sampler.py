from __future__ import annotations

import random


class MixedBatchSampler:
    def __init__(self, ratios: dict[str, int]) -> None:
        self.modalities = [k for k, v in ratios.items() if v > 0]
        self.weights = [max(1, int(ratios[k])) for k in self.modalities]

    def next_modality(self) -> str:
        return random.choices(self.modalities, weights=self.weights, k=1)[0]
