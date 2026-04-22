# Contract Escrow and Settlement

Contracts:

- `AICFJobEscrow`: `create_job`, `fund_job`, `reserve_budget`, `finalize_payout`, `refund_unused`, `cancel_job`.
- `AICFModelCall`: request/claim/commit/reference/accept/challenge/finalize/refund.
- `AICFAgentTask`: multi-step commitment tracking and finalization.

Settlement path:

1. Budget funded/reserved in ANM.
2. Provider commitment+result reference submitted.
3. Challenge window check.
4. Finalization computes payout/refund.
5. Reward/dispute hooks run.
