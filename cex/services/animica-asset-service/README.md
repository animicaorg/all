# Animica Asset Service

Native Animica (ANM) asset support for the centralized exchange, providing deposit and withdrawal functionality using a locally-run Animica node.

## Features

- ✅ **Deposit Detection**: Block scanning with confirmation tracking
- ✅ **Reorg Safety**: Automatic rollback and deposit invalidation on chain reorganizations
- ✅ **Withdrawal Broadcasting**: Native transaction building and broadcasting
- ✅ **Fee Management**: Dynamic fee estimation with configurable bounds
- ✅ **Idempotent Operations**: Safe retries and duplicate detection
- ✅ **Leader Election**: Multi-instance deployment with automatic failover
- ✅ **Comprehensive Monitoring**: Health checks, metrics, and audit logs

## Architecture

### Components

#### 1. Withdrawals (`src/withdrawals/`)
- **`fees.ts`**: Dynamic and fixed fee estimation
- **`build_tx.ts`**: Account-based transaction building with nonce tracking
- **`broadcast.ts`**: Transaction broadcasting via RPC
- **`tracker.ts`**: Status tracking and confirmation polling

#### 2. Deposits (`src/deposits/`)
- **`scanner.ts`**: Block scanning with reorg detection
- **`parser.ts`**: Transaction parsing for deposits
- **`reorg.ts`**: Reorg handling and rollback logic
- **`address_assign.ts`**: User deposit address creation

#### 3. Background Jobs (`src/jobs/`)
- **`scan_loop.ts`**: Blockchain scanning with leader election
- **`poll_withdrawals.ts`**: Pending withdrawal status updates
- **`reconcile.ts`**: Periodic reconciliation

#### 4. RPC Client (`src/rpc/`)
- **`client.ts`**: Robust JSON-RPC client with retries
- **`errors.ts`**: Error types and handling
- **`retry.ts`**: Exponential backoff retry logic
- **`types.ts`**: Type definitions

#### 5. Database (`src/db/`)
- **`repositories/`**: Data access layer for all entities
- Uses existing tables with Animica-specific extensions

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
