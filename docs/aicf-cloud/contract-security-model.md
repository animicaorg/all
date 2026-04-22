# Contract Job Security Model

Controls implemented:

- replay-safe nonces on contract transitions
- deterministic timeout/challenge windows
- anti-double-commit per provider/job
- anti-double-finalization guards
- pause controls and admin emergency hooks
- provider quarantine + stake slashing integration
- audit logs for funding, payout, refund, dispute, and pause actions

Integrity assumptions:

- result artifacts are referenced by hashes and signed metadata
- off-chain execution is untrusted until deterministic acceptance/finalization
