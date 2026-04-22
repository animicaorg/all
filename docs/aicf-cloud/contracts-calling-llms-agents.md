# Contracts Calling LLMs/Agents On Animica

AICF enables deterministic VM-PY contracts to orchestrate AI jobs without running nondeterministic inference inside consensus.

Pattern:

1. Contract escrows ANM budget.
2. Contract emits model-call or agent-task job intent.
3. Off-chain AICF provider network executes workload.
4. Providers submit result commitments/references.
5. Challenge/finalization rules execute deterministically on-chain.
6. Payout/refund/slash is settled in ANM.

This gives developers the practical behavior of "contracts calling AI" while preserving deterministic validation.
