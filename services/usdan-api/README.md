# USDAN API (`@animica/usdan-api`)

Production-oriented backend skeleton for USDAN fiat mint/redeem workflows.

## Capabilities

- Wallet-session auth and wallet binding
- KYC status and bank account gating
- Buy flow (`purchase intents` -> `fiat settled` -> `mint authorization`)
- Redemption flow (`request` -> `on-chain confirmation` -> `payout`)
- Modern Treasury provider abstraction (real + mock implementation)
- Webhook signature verification and idempotent processing
- Reserve dashboard snapshot reconciliation
- Admin operations, compliance flags, and support ticket surface
- Immutable audit log hooks for all critical actions

## Data model

See `prisma/schema.prisma` for all required entities:
`users`, `wallet_links`, `kyc_records`, `bank_account_records`, `purchase_intents`,
`redemption_requests`, `fiat_payment_events`, `mint_authorizations`,
`redemption_finality_records`, `reserve_snapshots`, `webhook_deliveries`,
`compliance_flags`, `admin_actions`, `support_tickets`, `audit_logs`, `idempotency_keys`.

## Commands

```bash
pnpm --filter @animica/usdan-api dev
pnpm --filter @animica/usdan-api build
pnpm --filter @animica/usdan-api test
pnpm --filter @animica/usdan-api db:generate
pnpm --filter @animica/usdan-api db:migrate
```

## Notes

- `USDAN_DATA_MODE=memory` is useful for local smoke tests.
- `USDAN_DATA_MODE=prisma` activates a Prisma-backed adapter surface.
- Modern Treasury webhook endpoint: `POST /webhooks/modern-treasury`.
