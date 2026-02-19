"""
ENA Credits System

Credit calculation and issuance for training contributions.
Includes quality bonuses, penalties, and anti-sybil measures.
"""

from .calculator import (
    CreditCalculator,
    QualityBonus,
    CreditResult,
    calculate_credits,
)
from .claim import ClaimManager, ClaimResult

__all__ = [
    "CreditCalculator",
    "QualityBonus",
    "CreditResult",
    "calculate_credits",
    "ClaimManager",
    "ClaimResult",
]
