"""ENA local ML pipeline (dataset, train, inference)."""

from .train.config import TrainerConfig
from .train.trainer import Trainer

__all__ = ["Trainer", "TrainerConfig"]
