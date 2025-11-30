# Devnet Consensus Parameters

This note captures the devnet-only consensus knobs so local nodes and CI bring-up remain deterministic and easy to mine.

## Block production
- **Target block time:** 2 seconds.
- **Genesis Θ (difficulty):** 2,000,000 µ-nats.
- **Θ floor / ceiling:** min 1,000,000 µ-nats, max 12,000,000 µ-nats.
- **Retarget smoothing:** EMA β = 0.45 over a 90-block window.
- **Per-step clamps:** Θ movement capped to **0.98×–1.15×** per retarget step.
- **Share micro-target:** 600,000 µ-nats.
- **Nullifier TTL:** 8,192 blocks.

These values keep a single auto-mining node progressing without getting stuck or letting Θ explode when miners join/leave.

## Rewards
- **Block subsidy:** 10,000,000 nANM (0.01 ANM) per block on devnet.
- **Split:** miner 60%, AICF 30%, treasury 10%.
- Fees remain zeroed by default for devnet smoke tests.

## References
- Spec source: `spec/params.yaml` (`animica:1337` network block).
- Devnet genesis templates: `tests/devnet/genesis_config.json`, `ops/k8s/configmaps/genesis.json`.
