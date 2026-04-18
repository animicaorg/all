# BANM Bridge Incident Runbook

## Immediate Controls

1. Pause affected direction:
   - `bridge_paused_forward=true` for ANM->BANM issue
   - `bridge_paused_reverse=true` for BANM->ANM issue
2. Pause contracts if required.
3. Snapshot impacted orders from admin UI/API.
4. Preserve logs and tx hashes.

## Incident Classes

### A. Wrong/Mismatched Deposits

- Move order to `MANUAL_REVIEW`.
- Confirm source/destination/amount mismatch from chain data.
- Contact operator to resolve per policy.

### B. Settlement Submit Failure

- Inspect worker logs and nonce state.
- Retry from admin action.
- If chain-side rejection persists, keep paused and escalate key/network diagnostics.

### C. Release Leg Failure (reverse flow)

- Keep reverse paused.
- Confirm BANM burn status and ANM release tx status.
- If burn succeeded but release failed, rerun release using idempotent order record.

### D. Solvency Discrepancy

- Trigger reconciliation run.
- Freeze reverse direction if reserve unavailable.
- Compare `banm_total_supply_wei`, pending liabilities, and custody balance.

## Recovery Steps

1. Confirm root cause.
2. Patch config/code.
3. Run targeted tests and dry-run orders.
4. Unpause gradually with canary traffic.
5. Publish post-incident summary.

