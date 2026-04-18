# BANM Bridge Architecture

## Product Scope

BANM is a custodial two-way bridge between Animica and BNB Chain:

1. `ANM_TO_BANM`
   - User deposits ANM on Animica.
   - Backend confirms deposit and required confirmations.
   - Backend mints BANM on BNB Chain to the signed EVM address.

2. `BANM_TO_ANM`
   - User signs order with MetaMask and deposits BANM through `BANMBridgeDepositRouter.deposit(orderId, amount)`.
   - Backend confirms deposit event and required confirmations.
   - Backend burns BANM from vault custody.
   - Backend releases ANM from Animica custody to immutable destination address.

## Components

- `python/animica/bridge_banm`: FastAPI API, order engine, worker scheduler, EIP-712 verification, reconciliation, admin controls.
- `evm/banm-bridge`: BANM token and bridge contracts (`BANMToken`, `BANMBridgeController`, `BANMBridgeVault`, `BANMBridgeDepositRouter`).
- `apps/banm-bridge-web`: Public MetaMask-first UI for order creation/signing/deposit/status.
- `apps/banm-bridge-admin`: Internal operations console.
- `ops/docker/banm-bridge`: Backend/web/admin containerization and compose templates.

## Order Binding Model

Each order stores immutable:

- `order_id`
- direction and chains
- source/destination addresses
- exact amount
- asset in/out
- unique deposit instructions
- expiry

Backend issues EIP-712 payload and verifies signature server-side. Settlement address on EVM must match recovered signer address.

## State Machine

`CREATED -> AWAITING_DEPOSIT -> DEPOSIT_SEEN -> CONFIRMING -> CONFIRMED -> READY_TO_SETTLE -> SETTLEMENT_SUBMITTED -> SETTLEMENT_CONFIRMED -> COMPLETED`

Exceptional states:

- `EXPIRED`
- `REJECTED`
- `FAILED`
- `MANUAL_REVIEW`
- `CANCELLED`

All transitions are recorded in `bridge_order_events` and `audit_logs`.

## Data Model

Core tables:

- `bridge_orders`
- `bridge_order_events`
- `bridge_deposits_animica`
- `bridge_deposits_evm`
- `bridge_signatures`
- `banm_mints`
- `banm_burns`
- `anm_releases`
- `chain_configs`
- `custody_wallets`
- `admin_users`
- `audit_logs`
- `service_locks`
- `idempotency_keys`
- `reconciliation_runs`

## Chain Adaptability

EVM integration is adapter-based:

- chain ID and RPC are configuration driven.
- contract addresses are environment driven.
- chain metadata lives in `evm/banm-bridge/config/chains/*.json` and `chain_configs`.

No deep business logic assumes BNB-only behavior.

