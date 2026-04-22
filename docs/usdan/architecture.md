# USDAN Architecture

## Components

1. **On-chain (VM-PY)**
- `USDANToken`: transfer/approve/mint/burn, pause/freeze/blocklist hooks.
- `USDANMintController`: backend-authorized mint execution with nonce/request replay protection.
- `USDANRedemptionController`: signed redemption intents, escrow/burn orchestration.
- `USDANComplianceController`: allowlist/denylist/sanctions + token control actions.
- `USDANReserveAttestation`: signed reserve statement commitments.

2. **Backend (`services/usdan-api`)**
- Auth/session + wallet binding.
- KYC and bank account gating.
- Buy intents, fiat settlement transitions, mint authorization signing.
- Redemption requests, on-chain confirmation, payout orchestration.
- Reserve snapshot derivation and reconciliation hashes.
- Webhook ingest + signature verification + idempotent processing.
- Admin/compliance/support and immutable audit trail hooks.

3. **Web app (`apps/usdan-web`)**
- Routes: `/`, `/buy`, `/redeem`, `/dashboard`, `/reserves`, `/transactions`, `/compliance`, `/faq`, `/support`, `/admin`.
- Animica browser wallet detection, connect, chain checks, message signing, add-token flow.

4. **Fiat/ledger provider**
- `TreasuryProvider` interface.
- `ModernTreasuryProvider` implementation.
- `MockTreasuryProvider` for tests/local boundaries.

## Core invariants

- No mint before settled fiat events.
- Mint auth is unique by `requestId` + `nonce`.
- Redemption is unique per user nonce and requires signed intent.
- Compliance flags can block user operations.
- Reserve snapshots capture supply/reserve/queues and reconciliation hash.
