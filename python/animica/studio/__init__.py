"""Animica Studio — serverless compute for the post-quantum chain.

Write a Python function, decorate it, and run it on Animica's decentralized
GPU/CPU fleet, paying in ANM. A Modal-style developer experience that rides on
Animica's live compute rails (AICF broker + provider fleet + on-chain payment).

    import animica.studio as studio

    app = studio.App("hello")

    @app.function(image=studio.Image.debian_slim().pip_install("numpy"), gpu="A100")
    def mean(seed: int) -> float:
        import numpy as np
        return float(np.random.default_rng(seed).standard_normal(1000).mean())

    mean.remote(7)            # dispatch one call
    list(mean.map(range(8)))  # fan-out
    mean.local(7)             # run here, no dispatch

Local mode runs your function in a subprocess sandbox with zero infrastructure,
so the SDK is demoable immediately; remote mode escrows ANM and dispatches to the
fleet. See ``ANIMICA_STUDIO_MODE`` (auto|local|remote).
"""

from __future__ import annotations

from . import errors
from .app import App, Function
from .config import Mode, StudioConfig, anm_to_nanos, nanos_to_anm
from .image import DEFAULT_IMAGE, Image
from .schedule import Cron, Period
from .secret import Secret
from .volume import Volume

__version__ = "2.0.0"

__all__ = [
    "App",
    "Function",
    "Image",
    "Secret",
    "Volume",
    "Cron",
    "Period",
    "StudioConfig",
    "Mode",
    "errors",
    "anm_to_nanos",
    "nanos_to_anm",
    "DEFAULT_IMAGE",
    "__version__",
]
