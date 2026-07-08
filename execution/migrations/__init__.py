"""One-time, height-gated state migrations applied during block execution.

Each migration is a deterministic, value-preserving state mutation that runs as
part of a specific block's transition (so every node computes the same result and
it re-applies cleanly on reorg/re-execution). Migrations must NEVER raise out of
block execution — a migration bug must not halt block import.
"""
