# Staking and Slashing

Provider staking:

- Stake ANM via `AICFStakeManager`.
- Min stake and unlock cooldown are governance parameters.
- Unstake flow: request -> cooldown -> finalize.

Slashing:

- Triggered by dispute outcome or admin safety hooks.
- Slash events reduce provider stake and affect reputation/quarantine state.
