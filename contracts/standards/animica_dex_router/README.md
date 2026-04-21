# Animica DEX Router (VM-PY)

`AnimicaDexRouter` is the user-facing execution contract for the Animica DEX.

## Responsibilities
- Pair creation request flow with launch fee forwarding.
- Quote helpers (`quote_exact_in`, `quote_exact_out`).
- Swap wrappers (`swap_exact_in`, `swap_exact_out`).
- Liquidity wrappers (`add_liquidity`, `remove_liquidity`) with token ordering normalization.

## Native Asset Convention
- Native ANM is represented as `b""` token address.
- Router enforces `abi.value()` consistency for operations involving ANM.

## Security Notes
- Router itself does not deploy contracts; pair bytecode deployment remains off-chain.
- Keep router owner behind multisig/governance.
