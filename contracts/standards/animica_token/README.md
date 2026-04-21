# Animica Token Standard (VM-PY)

`AnimicaTokenStandard` is the canonical reusable VM-PY fungible token contract for launchpad and wallet integrations.

## Features
- Name, symbol, decimals, metadata URI
- Fixed max supply with optional owner minting
- ERC20-style transfer/approve/allowance/transfer_from
- Burn and burn_from
- Freeze authority and account freeze checks
- Transfer/Mint/Burn/Approval/Metadata events

## Deployment args (`init`)
1. `name: bytes`
2. `symbol: bytes`
3. `decimals: int`
4. `owner: bytes`
5. `initial_supply: int`
6. `max_supply: int`
7. `mintable: bool`
8. `metadata_uri: bytes`
9. `freeze_authority: bytes` (empty bytes => owner)

## Notes
- This contract is designed to be instantiated per launched token.
- Metadata URI should point to IPFS JSON (e.g. `ipfs://...`).
- The launcher and wallet can use either snake_case or camelCase view aliases where provided.
