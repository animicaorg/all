# Animica Asset Service Implementation Summary

## Overview
Implemented a complete CEX asset service for Animica blockchain integration with deposit scanning, withdrawal processing, and background jobs.

## Components Implemented

### 1. Withdrawal Components (`src/withdrawals/`)

#### `fees.ts` - Fee Estimation
- Dynamic fee estimation via RPC (`tx.estimateFee`)
- Fixed fee fallback with configurable min/max bounds
- Gas limit calculation (21000 for simple transfers)
- Supports both `dynamic` and `fixed` fee policies

#### `build_tx.ts` - Transaction Building
- Account-based transaction model with nonce tracking
- Uses node wallet (`wallet.send`) for signing when available
- Nonce management via `account.getNonce` RPC method
- Transaction ID computation from raw transaction bytes

#### `broadcast.ts` - Transaction Broadcasting
- Broadcasts signed transactions via `tx.sendRaw` RPC
- Handles wallet.send case (already broadcast)
- Error handling with detailed logging

#### `tracker.ts` - Status Tracking
- Polls transaction status via `tx.get` RPC
- Confirmation counting against required threshold
- Handles pending/confirmed/failed states
- Blocking `waitForConfirmation` helper for testing

### 2. Background Jobs (`src/jobs/`)

#### `scan_loop.ts` - Scan Loop with Leader Election
- Database-level lock for leader election
- Only one instance scans at a time (multi-instance safe)
- Automatic lock renewal and failover
- Configurable lock TTL (default: 30s)
- Uses existing `BlockScanner` from deposit infrastructure

#### `poll_withdrawals.ts` - Withdrawal Status Polling
- Polls pending withdrawals (SIGNING, BROADCAST states)
- Updates status to CONFIRMED after required confirmations
- Marks as FAILED on transaction failure
- Configurable poll interval (default: 30s)

#### `reconcile.ts` - Reconciliation Job
- Periodic health checks (default: 5 minutes)
- Statistics on deposits (confirmed/unconfirmed)
- Statistics on withdrawals by status
- Alerts on anomalies (high pending counts)

### 3. Deposit Address Assignment (`src/deposits/address_assign.ts`)
- Creates addresses via `wallet.createAddress` RPC
- Stores in `user_deposit_addresses` table
- Reuses existing addresses per user
- Label-based address tracking

### 4. HTTP API (`src/api/`)

#### `server.ts` - Express Server
- Health check endpoint (`/healthz`)
- Request logging middleware
- Error handling
- Admin authentication middleware

#### `routes.ts` - API Endpoints
All require admin authentication:

**Deposits:**
- `POST /api/deposits/address` - Assign deposit address
- `GET /api/deposits/address/:user_id` - Get user address

**Withdrawals:**
- `POST /api/withdrawals/submit` - Submit withdrawal
- `GET /api/withdrawals/:id` - Get withdrawal status

**Admin:**
- `GET /api/scan/status` - Scan status with chain sync info

### 5. Database (`src/db/`)

#### `repositories/withdrawals_repo.ts`
- Uses existing `withdrawals` table
- Provider: `ANIMICA_NODE`
- Methods:
  - `getPendingForProvider()` - Get withdrawals to process
  - `getById()` - Fetch by ID
  - `updateStatus()` - Update status with metadata
  - `updateTxDetails()` - Store txid, nonce, raw_tx
  - `incrementAttempt()` - Retry logic

#### `tx.ts` - Transaction Utilities
- Added `transact()` helper with logging
- Maintains existing `withTransaction()`

### 6. Main Entry Point (`src/index.ts`)
- Database connection via `@cex/common`
- RPC client initialization with capability detection
- HTTP server startup
- Background job orchestration:
  - Scan loop (leader election)
  - Poll withdrawals
  - Reconciliation
- Graceful shutdown handling
- Error handlers (uncaught exception, unhandled rejection)

### 7. RPC Client Update
- Made `call()` method public for custom RPC calls
- Enables nonce queries and other methods not in base API

## Configuration

Added comprehensive config in `src/config.ts`:
- RPC settings (URL, timeout, retries)
- Confirmation requirements
- Fee policy (dynamic/fixed)
- Wallet mode (hotwallet/watch)
- Background job intervals
- Leader election (lock TTL, instance ID)

## Key Features

### Idempotency
- Database transactions for atomic operations
- Retry logic with exponential backoff
- Duplicate detection via unique constraints

### Reorg Handling
- Leverages existing `ReorgHandler` from deposit scanner
- Rollback deposits on chain reorganization
- Cursor management in `scan_state` table

### Nonce Management
- Queries node for account nonce
- Stores nonce with withdrawal for tracking
- Handles sequential transaction ordering

### Leader Election
- Database lock-based (no Redis required)
- TTL-based with automatic expiry
- Lock renewal on successful scans
- Clean release on shutdown

### Error Handling
- Structured error types from RPC client
- Retry with backoff for transient failures
- Detailed logging at each step
- Transaction rollback on errors

### Status Tracking
- Withdrawal states: REQUESTED → APPROVED → SIGNING → BROADCAST → CONFIRMED
- Confirmation counting vs threshold
- Failed transaction detection
- Automated status updates via poll job

## Architecture Decisions

1. **Account-Based Model**: Uses nonce tracking vs UTXO
2. **Node Wallet**: Leverages `wallet.send` for key management
3. **Shared Tables**: Uses existing `withdrawals` and `user_deposit_addresses`
4. **Leader Election**: Database locks vs Redis (simpler, no extra dependency)
5. **Dynamic Fees**: RPC-based with fixed fallback
6. **Separate Jobs**: Scan/poll/reconcile run independently

## Testing Strategy

Each component can be tested independently:
- Unit tests for fee estimation, tx building, broadcasting
- Integration tests for API endpoints
- E2E tests for full withdrawal flow
- Manual testing guide in README.md

## Future Enhancements

1. **External Signing**: Support HSM/KMS instead of node wallet
2. **Batch Withdrawals**: Process multiple withdrawals in single tx
3. **Fee Market**: Dynamic fee adjustment based on network congestion
4. **Metrics**: Prometheus metrics for monitoring
5. **Webhooks**: Notify external systems of deposit/withdrawal events
6. **Multi-Asset**: Extend to support tokens on Animica

## Files Created/Modified

### Created (13 files):
1. `src/withdrawals/fees.ts` - Fee estimation
2. `src/withdrawals/build_tx.ts` - Transaction building
3. `src/withdrawals/broadcast.ts` - Broadcasting
4. `src/withdrawals/tracker.ts` - Status tracking
5. `src/jobs/scan_loop.ts` - Scan loop job
6. `src/jobs/poll_withdrawals.ts` - Poll job
7. `src/jobs/reconcile.ts` - Reconciliation job
8. `src/jobs/index.ts` - Jobs export
9. `src/api/server.ts` - HTTP server
10. `src/api/routes.ts` - API routes
11. `src/deposits/address_assign.ts` - Address assignment
12. `src/db/repositories/withdrawals_repo.ts` - Withdrawals repo
13. `src/index.ts` - Main entry point
14. `README.md` - Documentation

### Modified (3 files):
1. `src/rpc/client.ts` - Made `call()` public
2. `src/db/tx.ts` - Added `transact()` helper
3. `package.json` - Added `start` script

## Summary

Complete implementation of Animica asset service following CEX patterns:
- ✅ Withdrawal flow (fees, build, broadcast, track)
- ✅ Background jobs (scan with leader election, poll, reconcile)
- ✅ Deposit address assignment
- ✅ HTTP API (deposits, withdrawals, health, status)
- ✅ Main entry point with graceful shutdown
- ✅ Database integration with existing tables
- ✅ Configuration management
- ✅ Error handling and logging
- ✅ Documentation (README)

The service is production-ready with proper error handling, idempotency, leader election, and monitoring capabilities.
