# USDAN Contracts (VM-PY)

This directory defines the production-oriented USDAN on-chain stack:

- `contracts/packages/usdan_token` - ANM20-like USDAN token with pause/freeze/blocklist hooks.
- `contracts/packages/usdan_mint_controller` - backend-signed mint authorization + replay protection.
- `contracts/packages/usdan_redemption_controller` - signed redemption intents + burn/escrow lifecycle.
- `contracts/packages/usdan_compliance_controller` - allowlist/denylist/sanctions + token control plane.
- `contracts/packages/usdan_reserve_attestation` - signed reserve snapshot commitments.

## Compile

```bash
python -m vm_py.cli.compile --manifest contracts/packages/usdan_token/manifest.json --out contracts/build/usdan_token/usdan_token.ir
python -m vm_py.cli.compile --manifest contracts/packages/usdan_mint_controller/manifest.json --out contracts/build/usdan_mint_controller/usdan_mint_controller.ir
python -m vm_py.cli.compile --manifest contracts/packages/usdan_redemption_controller/manifest.json --out contracts/build/usdan_redemption_controller/usdan_redemption_controller.ir
python -m vm_py.cli.compile --manifest contracts/packages/usdan_compliance_controller/manifest.json --out contracts/build/usdan_compliance_controller/usdan_compliance_controller.ir
python -m vm_py.cli.compile --manifest contracts/packages/usdan_reserve_attestation/manifest.json --out contracts/build/usdan_reserve_attestation/usdan_reserve_attestation.ir
```

## Deployment Sequence

1. Deploy `usdan_token`
2. Deploy `usdan_compliance_controller`
3. Deploy `usdan_mint_controller`
4. Deploy `usdan_redemption_controller`
5. Deploy `usdan_reserve_attestation`
6. Set token controller addresses (`set_compliance_controller`, `set_mint_controller`, `set_redemption_controller`)
7. Grant operator and signer roles in controllers
8. Enable compliance allowlist mode if required (`set_allowlist_enforced(true)`)

## Safety invariants

- Minting only succeeds from the mint controller with valid backend signatures.
- Mint request IDs and nonces are single-use.
- Redemption requests are nonce-protected per user.
- Cancelation is only possible in escrow mode.
- Compliance controller can push pause/freeze/blocklist state to token.
- Reserve attestations are immutable once submitted per ID.
