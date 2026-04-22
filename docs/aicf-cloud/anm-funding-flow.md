# ANM Funding Flow

1. User links wallet.
2. Dashboard calls `POST /projects/:id/fund` with ANM nanos amount.
3. API records project ledger increase and returns contract call payload.
4. Browser wallet submits deposit transaction to `AICFProjectBalance` contract.
5. Jobs reserve budgets from project available balance into escrow.
