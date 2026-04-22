# USDAN Contracts

## Paths

- `contracts/packages/usdan_token`
- `contracts/packages/usdan_mint_controller`
- `contracts/packages/usdan_redemption_controller`
- `contracts/packages/usdan_compliance_controller`
- `contracts/packages/usdan_reserve_attestation`

## Build

```bash
./contracts/usdan/scripts/build_all.sh
```

## Contract test command

```bash
pytest -q contracts/tests/test_usdan_contracts.py
```

## Security controls included

- Mint role isolation through mint controller address.
- Nonce and request replay locks in mint controller.
- Redemption per-user nonce replay lock.
- Pause/freeze/blocklist in token and compliance controller.
- Allowlist enforcement mode.
- Reserve attestation signature checks and immutable attestation IDs.
