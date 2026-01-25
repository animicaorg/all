# Animica CEX Architecture (Scaffold)

## Overview
This monorepo defines a modular CEX for Animica (ANM) plus multi-chain assets. The system is event-driven with NATS (JetStream-ready), PostgreSQL for durable state, Redis for rate limits/sessions, and strict separation between matching, ledger, and wallet routing.

Key requirements implemented in this scaffold:
- Deterministic matching engine independent of database latency.
- Ledger is the sole authority to mutate balances via double-entry journal.
- BitGo handles supported assets (BTC, ETH, ERC-20, etc.).
- Animica uses a local `animica-node` over localhost RPC (no BitGo).

## Service boundaries & ownership
### 1) api-gateway (public)
- Public REST + WebSocket market data (stubbed now).
- Authenticated endpoints for orders/balances (stubbed now).
- Issues **command messages** to `matching-engine` and reads materialized views from DB.
- **Never** directly mutates balances.

### 2) auth-service
- Owns users/credentials tables.
- Handles registration, login, API keys (stubbed now).
- Future 2FA/OAuth extensions.

### 3) matching-engine
- Owns in-memory order books.
- Deterministic price-time matching.
- Consumes `OrderCommand` and emits `TradeEvent` + `OrderEvent`.
- Optional snapshots for recovery; **ledger** remains source of truth.
- Enforces idempotent order submission via `clientOrderId`.

### 4) ledger-service
- **Only** service that mutates balances.
- Consumes `TradeEvent`, `DepositConfirmed`, `WithdrawalSettled`.
- Maintains immutable journal (double-entry), and materialized balances.
- Strict idempotency via `event_id` + `processed_events` table.
- Exposes read APIs for balances and accounting reports (stubbed now).

### 5) wallet-router
- Unified interface for withdrawals and deposit address assignment.
- Routes to BitGo (supported assets) or Animica connector (ANM).
- Enforces withdrawal policy and queues approvals (stubbed now).

### 6) bitgo-webhook-ingestor
- HTTP receiver for BitGo webhooks.
- Validates signatures + correlation IDs (stubbed now).
- Emits normalized blockchain events to NATS.

### 7) animica-indexer
- Talks to local `animica-node` RPC over `localhost`.
- Scans blocks for deposits to exchange-controlled addresses.
- Handles confirmations and reorg safety.
- Emits normalized blockchain events to NATS.

### 8) risk-service (stub)
- AML/risk checks, withdrawal holds, velocity limits.
- Consumes events, publishes decisions.

### 9) admin-service (stub)
- Admin APIs for approvals, policies, market management.

## Data ownership
- **Postgres** single cluster initially, one schema per service (future). Ledger schema is canonical for balances.
- **Ledger** is the only writer of balance tables.
- **Matching engine** stores order/trade views only; ledger/journal is canonical for money.
- **Redis** for rate limits, sessions, ephemeral caches.
- **NATS** for command/event bus (JetStream-ready).

## Text diagram (high level)
```
Clients
  │
  │ REST/WS
  ▼
api-gateway ──────► NATS (commands/events) ──────► matching-engine
  │                                             │
  │ reads views                                  ├─► trade/order events
  ▼                                             │
Postgres (views)                                 ▼
                                           ledger-service ──► balances/journal
                                                  │
                                                  ├─► wallet-router ─┬─► BitGo
                                                  │                  └─► Animica RPC
                                                  └─► audit/logging
```

## Key flows
### Place order
1. API receives order request and validates idempotency key (`clientOrderId`).
2. API publishes `OrderSubmit` command to `cex.order.submit`.
3. Matching engine consumes `OrderSubmit`, matches deterministically, emits `OrderAccepted`, `OrderUpdated`, `TradeExecuted`.
4. Ledger consumes `OrderAccepted` / `TradeExecuted`, writes immutable journal entries, updates balances.
5. API reads updated balances from materialized tables.

### Deposit via BitGo
1. BitGo webhook -> `bitgo-webhook-ingestor`.
2. Ingestor validates signature + correlation ID.
3. Emits `DepositSeen`/`DepositConfirmed` to NATS.
4. Ledger credits user balance on `DepositConfirmed`.

### Withdraw via BitGo
1. API issues `WithdrawRequest` command.
2. `wallet-router` applies policy and risk/admin approvals.
3. `wallet-router` triggers BitGo withdrawal.
4. BitGo webhooks update status (`WithdrawalBroadcast`, `WithdrawalSettled`).
5. Ledger debits and settles balances on final confirmation.

### Deposit via Animica
1. `animica-indexer` scans local Animica RPC.
2. Emits `DepositConfirmed` after confirmations.
3. Ledger credits user balance.

### Withdraw via Animica
1. API issues `WithdrawRequest` to wallet-router.
2. `wallet-router` uses Animica RPC to broadcast.
3. Indexer observes confirmations.
4. Ledger settles withdrawal.

## Idempotency rules
- Every command/event includes: `event_id`, `correlation_id`, `causation_id`, `created_at`.
- Producers include `idempotency_key` (`clientOrderId`, `withdrawalId`, etc.).
- Consumers persist processed `event_id`s in `processed_events` to avoid double-application.

## Message bus subjects (NATS)
### Commands
- `cex.order.submit`
- `cex.order.cancel`
- `cex.withdraw.request`
- `cex.withdraw.approve`
- `cex.withdraw.reject`
- `cex.wallet.address.assign`

### Events
- `cex.order.accepted`
- `cex.order.rejected`
- `cex.order.updated`
- `cex.trade.executed`
- `cex.deposit.seen`
- `cex.deposit.confirmed`
- `cex.deposit.reorged`
- `cex.withdraw.created`
- `cex.withdraw.broadcast`
- `cex.withdraw.confirmed`
- `cex.withdraw.failed`
- `cex.ledger.entry.posted`
- `cex.balance.updated`

### Consumer groups / durable subscriptions
- Use NATS JetStream durable consumers per service group:
  - `matching-engine`
  - `ledger-service`
  - `wallet-router`
  - `risk-service`
  - `bitgo-webhook-ingestor`
  - `animica-indexer`

## Repo layout
```
/cex
  /docs
    architecture.md
    local-dev.md
    security-notes.md
  /ops
    docker-compose.yml
    env/.env.example
    scripts/dev-up.sh
    scripts/dev-down.sh
    scripts/migrate.sh
    scripts/seed.sh
  /packages
    /common
      src/types
      src/config
      src/logger
      src/errors
      src/crypto
      src/time
    /db
      src/migrations
      src/seeds
  /services
    /api-gateway
    /auth-service
    /matching-engine
    /ledger-service
    /wallet-router
    /bitgo-webhook-ingestor
    /animica-indexer
    /risk-service
    /admin-service
  package.json
  pnpm-workspace.yaml
  tsconfig.base.json
```
