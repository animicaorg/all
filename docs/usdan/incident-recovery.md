# Incident Recovery

## Trigger scenarios

- Webhook signature failures spike
- Reserve coverage below threshold
- Mint mismatch between settled fiat and on-chain supply
- Redemption payout failures / stuck queue

## Recovery checklist

1. Pause token via compliance controls.
2. Freeze high-risk accounts if required.
3. Stop automatic mint transitions.
4. Reconcile ledger, supply, pending queues.
5. Replay failed webhooks idempotently.
6. Resume flows gradually and publish incident snapshot.

## Data to preserve

- webhook deliveries
- fiat payment events
- mint authorization statuses
- redemption statuses
- admin/audit logs
