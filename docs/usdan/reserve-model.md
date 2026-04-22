# Reserve Model

Reserve model is derived by `ReserveService`:

- `tokenSupply`: on-chain value from chain read client
- `reserveLedgerBalance`: settled USD balance from treasury ledger
- `pendingMintQueue`: buy intents in `FUNDS_SETTLED|MINT_AUTHORIZED|MINT_SUBMITTED`
- `outstandingRedemptionQueue`: redemptions in `ONCHAIN_PENDING|ONCHAIN_CONFIRMED|PAYOUT_PENDING|PAYOUT_SENT`
- `coverageRatioBps = reserveLedgerBalance / tokenSupply * 10_000`
- `reconciliationHash = sha256(snapshot inputs)`

Snapshots are stored as `reserve_snapshots` records and exposed via `/reserves/dashboard`.
