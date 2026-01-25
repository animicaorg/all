# Animica Asset Service

Centralized exchange integration for Animica blockchain.

## Components Implemented

### 1. Withdrawals (`src/withdrawals/`)
- **`fees.ts`**: Dynamic and fixed fee estimation
- **`build_tx.ts`**: Account-based transaction building with nonce tracking
- **`broadcast.ts`**: Transaction broadcasting via RPC
- **`tracker.ts`**: Status tracking and confirmation polling

### 2. Background Jobs (`src/jobs/`)
- **`scan_loop.ts`**: Blockchain scanning with leader election
- **`poll_withdrawals.ts`**: Pending withdrawal status updates
- **`reconcile.ts`**: Periodic reconciliation of deposits/withdrawals

### 3. Deposit Address Assignment (`src/deposits/`)
- **`address_assign.ts`**: User deposit address creation and management

### 4. HTTP API (`src/api/`)
- **`server.ts`**: Express server setup
- **`routes.ts`**: RESTful endpoints for deposits and withdrawals

### 5. Database
- **`db/repositories/withdrawals_repo.ts`**: Withdrawal data access
- Uses existing `withdrawals` table with `provider="ANIMICA_NODE"`

## API Endpoints

All endpoints require admin authentication via `Authorization: Bearer <token>`.

### Deposits

- **POST `/api/deposits/address`**: Assign deposit address to user
  ```json
  {
    "user_id": "user123",
    "asset_network_id": "ffffffff-0006-0006-0006-000000000006",
    "label": "optional_label"
  }
  ```

- **GET `/api/deposits/address/:user_id`**: Get user's deposit address

### Withdrawals

- **POST `/api/withdrawals/submit`**: Submit withdrawal for processing
  ```json
  {
    "withdrawal_id": "uuid",
    "from_address": "animica_address",
    "to_address": "destination_address",
    "amount": "1000000000000000000"
  }
  ```

- **GET `/api/withdrawals/:id`**: Get withdrawal status

### Admin

- **GET `/api/scan/status`**: Get blockchain scan status
- **GET `/healthz`**: Health check (no auth required)

## Configuration

Environment variables (see `src/config.ts`):

```bash
# Animica RPC
ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc
ANIMICA_NETWORK=mainnet
ANIMICA_ASSET_NETWORK_ID=ffffffff-0006-0006-0006-000000000006

# Confirmations
ANIMICA_CONFIRMATIONS_REQUIRED=20
ANIMICA_SCAN_START_HEIGHT=0

# Fee policy
ANIMICA_FEE_POLICY=dynamic  # or "fixed"
ANIMICA_MIN_FEE_ATOMS=1000000000000000
ANIMICA_MAX_FEE_ATOMS=100000000000000000

# Wallet
ANIMICA_WALLET_MODE=hotwallet
ANIMICA_HOT_WALLET_LABEL=exchange_hot

# Admin
ADMIN_API_KEY=<secret>

# Database
DATABASE_URL=postgresql://user:pass@localhost/cex
```

## Running

```bash
# Development
npm run dev

# Production
npm run build
npm start
```

## Architecture

### Deposit Flow
1. User requests deposit address via API
2. Service creates address on Animica node (if new) or returns existing
3. Scanner detects deposits to tracked addresses
4. Deposits are credited to user accounts after confirmations

### Withdrawal Flow
1. Withdrawal request created in `withdrawals` table (status: APPROVED)
2. API `/api/withdrawals/submit` is called
3. Fee estimation (dynamic or fixed)
4. Transaction building with nonce tracking
5. Broadcasting to network (status: BROADCAST)
6. Poll job tracks confirmations (status: CONFIRMED)

### Leader Election
Scan loop uses database-level locks for leader election:
- Only one instance scans at a time
- Lock TTL: 30 seconds (configurable)
- Automatic failover if leader dies

## Key Features

- **Idempotent operations**: Safe retries throughout
- **Reorg handling**: Deposit scanner handles chain reorganizations
- **Nonce management**: Tracks transaction nonces for account-based model
- **Leader election**: Multi-instance deployment support
- **Dynamic fees**: RPC-based or fixed fee policies
- **Status tracking**: Automated confirmation polling
- **Reconciliation**: Periodic health checks and alerting
