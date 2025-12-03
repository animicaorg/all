# Animica Mainnet Tokenomics

## Mainnet Parameters

- **Chain ID / Network**: `1` (Animica Mainnet)
- **Target block time**: 2 seconds
- **Block limits**: 2 MiB envelope (`max_bytes`) and 40,000,000 gas (`max_gas`)
- **Genesis premine**: 81,000,000 ANM (9 decimals → base unit is nano-ANM)
- **Emission schedule toward max supply (900,000,000 ANM)**:
  - Initial block reward: **5.199141213 ANM** (5,199,141,213 nANM) per block
  - Halving cadence: **50%** reduction every **78,840,000 blocks** (~5 years at 2s)
  - Duration: **10 halving epochs (~50 years total chain issuance)** — rewards decay to zero once the 900M ANM cap (81M premine + 819M mined) is reached at the end of the 10th epoch
  - Tail emission: none after the cap is hit
- **Premine allocations (genesis balances)**:
  - `system:foundation` — **45,000,000 ANM** (45,000,000 × 10^9 nANM)
  - `system:treasury` — **20,000,000 ANM** (20,000,000 × 10^9 nANM)
  - `system:aicf` — **7,000,000 ANM** (7,000,000 × 10^9 nANM)
  - `system:founder` — **9,000,000 ANM** (9,000,000 × 10^9 nANM)
