# BANM Bridge Threat Model

## Trust Assumptions

- Custodial operators control settlement keys and custody wallets.
- EVM signatures prove EVM address control only.
- Without Animica-side signing, common ownership of Animica and EVM addresses is not fully proven.

## Main Threats

1. Double mint
   - Mitigation: on-chain order ID replay guards + backend idempotency + unique order transitions.

2. Double release
   - Mitigation: release record uniqueness + state machine + capped release.

3. Replay / duplicate deposits
   - Mitigation: unique tx hashes in deposit tables + exact amount/source checks + manual review for mismatch.

4. Sender spoofing
   - Mitigation: EIP-712 signer verification and tx sender equality checks for BANM deposit path.

5. Address mutation after order creation
   - Mitigation: immutable order fields, no update endpoint for destination/source.

6. Reorg handling
   - Mitigation: configurable confirmation depth and delayed settlement.

7. Operator key compromise
   - Mitigation: pause switches, daily caps, audit logs, rotation procedures, least-privilege roles.

8. Insolvency drift
   - Mitigation: reconciliation jobs + solvency reporting + reserve checks before release.

## Manual Review Triggers

- wrong source address
- wrong destination address
- wrong amount
- wrong router/token/order ID event
- failed settlement tx
- missing settlement references

Orders in manual review never auto-settle.

