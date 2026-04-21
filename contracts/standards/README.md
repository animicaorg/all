# Animica Token + DEX Standards

This directory contains reusable VM-PY standards used by the Animica launcher and DEX stack.

- `animica_token`: Fungible token standard with metadata URI, cap controls, approvals, mint/burn, freeze authority.
- `animica_dex_pair`: Constant-product AMM pair with LP accounting.
- `animica_dex_factory`: Pair registry and fee policy.
- `animica_dex_router`: User-facing pair creation + swap + liquidity router.

These standards are designed for direct deployment via `omni_sdk.contracts.deployer.deploy_package` using each contract's `manifest.json` and `contract.py`.
