# Animica DEX Pair (VM-PY)

`AnimicaDexPair` is the standard Animica constant-product AMM pair contract.

## Features
- Token/token and ANM/token pools (`b""` token sentinel = native ANM).
- LP accounting (`lp_total_supply`, `lp_balance_of`).
- Add/remove liquidity with explicit LP owner and payer addresses.
- Swap exact-in and exact-out.
- Configurable fee bps (owner-controlled).
- Deterministic events for indexers.

## Important Notes
- Pair deployment is done off-chain; registration and discovery are handled by `AnimicaDexFactory`.
- Use `AnimicaDexRouter` for user flows so token ordering and fee forwarding stay consistent.
- For non-native tokens, liquidity/swap token movement uses `transfer_from`; users must approve the pair spender (the chain ops script does this automatically before router calls).
- Contract-level owner should be an ops/governance address, not an EOA hot key.
