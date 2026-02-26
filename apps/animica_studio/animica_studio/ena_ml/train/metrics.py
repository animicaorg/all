from __future__ import annotations

import math


def perplexity(loss: float) -> float:
    return float(math.exp(min(20.0, max(-20.0, loss))))
