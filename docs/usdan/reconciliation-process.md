# Reconciliation Process

`ReconciliationJob` periodically calls `ReserveService.captureSnapshot('RECONCILIATION')`.

## Inputs

- On-chain total supply
- Treasury settled ledger balance
- Pending mint queue from purchase intents
- Outstanding redemption queue from redemption requests

## Outputs

- `reserve_snapshots` record
- `coverageRatioBps`
- `reconciliationHash`

## Operational policy

- Reconcile at least every minute in production.
- Publish signed reserve attestation digest daily (or tighter SLA).
- Alert if coverage ratio < configured threshold.
