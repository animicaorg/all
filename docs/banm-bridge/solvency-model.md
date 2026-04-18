# BANM Solvency Model

## Core Invariant

`BANM liabilities <= ANM reserve + in-flight adjustments`

Tracked public metrics:

- confirmed ANM reserve balance
- BANM total supply
- pending ANM->BANM mint liabilities
- pending BANM->ANM release liabilities
- effective liabilities
- available redeemable reserve

## Accounting Rules

1. Successful `ANM_TO_BANM`:
   - reserve increases on confirmed Animica deposit
   - BANM mint recorded once

2. Successful `BANM_TO_ANM`:
   - BANM deposit confirmed
   - BANM burned/retired once
   - ANM release recorded once

3. No order may execute both mint and release paths.

4. Replay prevention:
   - unique order IDs
   - unique tx hash records
   - unique settlement rows per order

## Reconciliation

Periodic job stores summary in `reconciliation_runs`.

Discrepancies trigger manual investigation and directional pause if needed.

