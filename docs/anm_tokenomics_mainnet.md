# Animica Mainnet Tokenomics

## Mainnet Parameters

- **Chain ID / Network**: `1` (Animica Mainnet)
- **Target block time**: 2 seconds
- **Block limits**: 2 MiB envelope (`max_bytes`) and 40,000,000 gas (`max_gas`)
- **Genesis premine**: 81,000,000 ANM (9 decimals → base unit is nano-ANM)
- **Emission schedule toward max supply (900,000,000 ANM)**:
  - Initial block reward: **5.1941 ANM** (5,194,100,000 nANM) per block
  - Halving cadence: **50%** reduction every **78,840,000 blocks** (~5 years at 2s)
  - Duration: halving over ~10 epochs (~50 years) approaches the 900M ANM cap with ~819M ANM emitted after genesis
  - Tail emission: effectively trends toward zero after repeated halvings
- **Premine allocations (genesis balances)**:
  - `system:foundation` — **45,000,000 ANM** (45,000,000 × 10^9 nANM)
  - `system:treasury` — **20,000,000 ANM** (20,000,000 × 10^9 nANM)
  - `system:aicf` — **7,000,000 ANM** (7,000,000 × 10^9 nANM)
  - `system:founder` — **9,000,000 ANM** (9,000,000 × 10^9 nANM)
