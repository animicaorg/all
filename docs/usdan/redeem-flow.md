# Redemption Flow

1. User connects wallet, has approved KYC + verified bank.
2. User submits redemption request (`/redeem/requests`) with signed intent hash.
3. On-chain redemption action occurs through `USDANRedemptionController` (escrow or burn mode).
4. Backend validates on-chain confirmation (`markOnchainConfirmed`).
5. Backend creates payout with `TreasuryProvider.createPayout`.
6. Webhook/manual update marks payout settled (`markPayoutSettled`).
7. Request moves to `COMPLETED` with full audit trail.

Cancellation is controller-governed and escrow-mode constrained on-chain.
