# Buy Flow

1. User connects wallet and creates API session.
2. User must have `KYC=APPROVED` and verified bank account.
3. User creates purchase intent (`/buy/intents`).
4. Backend creates inbound fiat operation via `TreasuryProvider.createInboundFunding`.
5. On settled event (`markFundsSettled` or webhook), backend transitions to `FUNDS_SETTLED`.
6. Backend prepares and signs mint authorization (`MintAuthorizationService`).
7. On-chain mint execution occurs through `USDANMintController.execute_mint`.
8. Backend marks mint submitted/confirmed and history updates.

Minting is blocked until step 5 completes.
