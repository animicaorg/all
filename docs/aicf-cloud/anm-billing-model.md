# ANM Billing Model

Billing is ANM-native end to end:

- Project balances are denominated in ANM nanos.
- Jobs reserve max budgets before execution.
- On completion, charged amount is settled and unused budget refunded.
- Usage record stores charged amount, provider reward share, treasury share, subsidy share.
- Settlement record can be queued/anchored on chain.
