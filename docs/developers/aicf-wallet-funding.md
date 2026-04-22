# AICF Wallet and ANM Funding

AICF uses ANM-native balances and escrow.

## Funding flow

1. Connect wallet in app.
2. Transfer ANM to project balance contract.
3. Confirm project balance update.
4. Run API calls or queue jobs.

## Billing behavior

- Calls and jobs reserve ANM budget before execution.
- Final settlement debits actual usage and releases provider rewards.
- Usage rows include settlement identifiers for auditability.
