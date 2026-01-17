"""
Animica Mining Pool with PPLNS Payouts.

This package implements a fully-featured mining pool with:
- Pay Per Last N Shares (PPLNS) payout mode
- Share accounting and validation
- Block tracking with confirmation and orphan detection
- Automatic payout engine with batching and retry
- VarDiff (variable difficulty) per miner
- Abuse prevention (banning, rate limiting)
- Comprehensive stats and metrics
- HTTP API and optional web dashboard
"""

from __future__ import annotations

__version__ = "0.1.0"
