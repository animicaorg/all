# Exchange API Database Schema Documentation

## Overview

The Exchange API uses a PostgreSQL database with 35 tables organized into logical groups. The schema implements strict double-entry accounting principles with comprehensive audit trails and idempotency guarantees.

## Schema Diagram (Conceptual)

```
┌─────────────────────────────────────────────────────────────────┐
│                         USERS & AUTH                             │
│  users → user_profiles, api_keys, sessions                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                          KYC / AML                               │
│  kyc_cases → kyc_documents                                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                     ASSETS & NETWORKS                            │
│  networks ← asset_networks → assets                             │
│           ↓                                                      │
│  wallets, user_deposit_addresses                                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      MARKETS & TRADING                           │
│  markets → orders → order_events                                │
│           ↓                                                      │
│         trades                                                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  LEDGER (DOUBLE-ENTRY) ⭐️                       │
│  ledger_accounts ← ledger_entries → ledger_transactions         │
│           ↓                                                      │
│    balances_cache                                               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                  DEPOSITS & WITHDRAWALS                          │
│  deposits, withdrawals → withdrawal_approvals                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                      SYSTEM & AUDIT                              │
│  fee_schedules, audit_logs, idempotency_keys                    │
└─────────────────────────────────────────────────────────────────┘
```

## Table Definitions

### USERS & AUTH

#### users
Primary user accounts table.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | User ID |
| email | citext | UNIQUE, NULLABLE | Email (lowercase) |
| phone | text | UNIQUE, NULLABLE | Phone number |
| password_hash | text | NULLABLE | Hashed password |
| status | enum | NOT NULL | ACTIVE, SUSPENDED, CLOSED |
| role | enum | NOT NULL | USER, ADMIN, OPS, COMPLIANCE |
| twofa_enabled | boolean | NOT NULL | 2FA status |
| created_at | timestamptz | NOT NULL | Creation timestamp |
| updated_at | timestamptz | NOT NULL | Update timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- UNIQUE on `email`
- UNIQUE on `phone`

#### user_profiles
Extended user profile information.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| user_id | uuid | PK, FK(users.id) | User reference |
| display_name | text | NULLABLE | Display name |
| country | text | NULLABLE | Country code |
| region | text | NULLABLE | State/region |
| legal_name | text | NULLABLE | Legal name |
| created_at | timestamptz | NOT NULL | Creation timestamp |
| updated_at | timestamptz | NOT NULL | Update timestamp |

**Foreign Keys:**
- `user_id` → `users.id` CASCADE

#### api_keys
API key management for programmatic access.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Key ID |
| user_id | uuid | FK(users.id) | Owner |
| name | text | NOT NULL | Key name |
| key_id | text | UNIQUE | Public key identifier |
| secret_hash | text | NOT NULL | Hashed secret |
| scopes | jsonb | NOT NULL | Permissions array |
| ip_allowlist | jsonb | NULLABLE | Allowed IPs |
| revoked_at | timestamptz | NULLABLE | Revocation timestamp |
| created_at | timestamptz | NOT NULL | Creation timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `user_id`
- UNIQUE on `key_id`

#### sessions
User login sessions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Session ID |
| user_id | uuid | FK(users.id) | User reference |
| refresh_token_hash | text | NOT NULL | Hashed token |
| expires_at | timestamptz | NOT NULL | Expiration time |
| created_at | timestamptz | NOT NULL | Creation timestamp |
| revoked_at | timestamptz | NULLABLE | Revocation timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `user_id`
- INDEX on `expires_at`

### KYC / AML

#### kyc_cases
KYC verification workflows.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Case ID |
| user_id | uuid | FK(users.id) | User reference |
| provider | enum | NOT NULL | BITGO, SUMSUB, MANUAL, NONE |
| status | enum | NOT NULL | NOT_STARTED, PENDING, VERIFIED, REJECTED, REVIEW |
| risk_tier | enum | NULLABLE | LOW, MEDIUM, HIGH |
| submitted_at | timestamptz | NULLABLE | Submission time |
| reviewed_at | timestamptz | NULLABLE | Review completion time |
| reviewer_user_id | uuid | FK(users.id) | Reviewer reference |
| notes | text | NULLABLE | Internal notes |
| created_at | timestamptz | NOT NULL | Creation timestamp |
| updated_at | timestamptz | NOT NULL | Update timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `user_id`
- INDEX on `status`

#### kyc_documents
Uploaded KYC documents.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Document ID |
| kyc_case_id | uuid | FK(kyc_cases.id) | Case reference |
| doc_type | text | NOT NULL | Document type |
| storage_ref | text | NOT NULL | S3/storage reference |
| sha256 | text | NOT NULL | File hash |
| created_at | timestamptz | NOT NULL | Upload timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `kyc_case_id`

### ASSETS & NETWORKS

#### networks
Blockchain networks.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Network ID |
| code | text | UNIQUE | Network code (BTC, ETH, ANIMICA) |
| kind | enum | NOT NULL | UTXO, EVM, SOLANA, ANIMICA, OTHER |
| chain_id | text | NULLABLE | Chain ID (EVM, Animica) |
| rpc_url | text | NULLABLE | Node RPC endpoint |
| confirmations_required | int | NOT NULL | Confirmations needed |
| created_at | timestamptz | NOT NULL | Creation timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- UNIQUE on `code`

#### assets
Tradable assets.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Asset ID |
| symbol | text | UNIQUE | Asset symbol (BTC, ETH, USDT) |
| name | text | NOT NULL | Full name |
| decimals | int | NOT NULL | Decimal precision |
| kind | enum | NOT NULL | NATIVE, TOKEN |
| is_enabled | boolean | NOT NULL | Trading enabled |
| created_at | timestamptz | NOT NULL | Creation timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- UNIQUE on `symbol`

#### asset_networks
Asset availability per network.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Record ID |
| asset_id | uuid | FK(assets.id) | Asset reference |
| network_id | uuid | FK(networks.id) | Network reference |
| contract_address | text | NULLABLE | Token contract (ERC20, SPL) |
| deposit_enabled | boolean | NOT NULL | Deposits allowed |
| withdrawal_enabled | boolean | NOT NULL | Withdrawals allowed |
| min_withdrawal | decimal(38,18) | NOT NULL | Minimum withdrawal |
| withdrawal_fee | decimal(38,18) | NOT NULL | Base withdrawal fee |

**Constraints:**
- UNIQUE on `(asset_id, network_id)`

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `asset_id`
- INDEX on `network_id`

#### wallets
House wallets for custody.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Wallet ID |
| network_id | uuid | FK(networks.id) | Network reference |
| purpose | enum | NOT NULL | HOT, WARM, COLD, TREASURY, FEE |
| provider | enum | NOT NULL | BITGO, LOCAL_ANIMICA, OTHER |
| provider_ref | text | NOT NULL | Provider wallet ID |
| address | text | NULLABLE | Wallet address |
| is_active | boolean | NOT NULL | Active status |
| created_at | timestamptz | NOT NULL | Creation timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `network_id`
- INDEX on `(provider, provider_ref)`

#### user_deposit_addresses
Per-user deposit addresses.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Address ID |
| user_id | uuid | FK(users.id) | User reference |
| asset_network_id | uuid | FK(asset_networks.id) | Asset/network combo |
| address | text | NOT NULL | Deposit address |
| tag | text | NULLABLE | Memo/tag (XRP, etc.) |
| assigned_wallet_id | uuid | FK(wallets.id) | House wallet |
| status | enum | NOT NULL | ACTIVE, ROTATED, DISABLED |
| created_at | timestamptz | NOT NULL | Creation timestamp |

**Constraints:**
- UNIQUE on `(asset_network_id, address, COALESCE(tag, ''))`

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `user_id`
- INDEX on `asset_network_id`

### MARKETS & TRADING

#### markets
Trading pairs.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Market ID |
| symbol | text | UNIQUE | Market symbol (BTC-USD) |
| base_asset_id | uuid | FK(assets.id) | Base asset |
| quote_asset_id | uuid | FK(assets.id) | Quote asset |
| status | enum | NOT NULL | ONLINE, HALTED, READONLY |
| price_tick | decimal(38,18) | NOT NULL | Min price increment |
| size_step | decimal(38,18) | NOT NULL | Min size increment |
| min_order_size | decimal(38,18) | NOT NULL | Minimum order size |
| created_at | timestamptz | NOT NULL | Creation timestamp |

**Constraints:**
- UNIQUE on `(base_asset_id, quote_asset_id)`

**Indexes:**
- PRIMARY KEY on `id`
- UNIQUE on `symbol`
- INDEX on `status`

#### orders
User orders.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Order ID |
| user_id | uuid | FK(users.id) | User reference |
| market_id | uuid | FK(markets.id) | Market reference |
| side | enum | NOT NULL | BUY, SELL |
| type | enum | NOT NULL | LIMIT, MARKET, STOP_LIMIT, STOP_MARKET |
| time_in_force | enum | NOT NULL | GTC, IOC, FOK, POST_ONLY |
| status | enum | NOT NULL | OPEN, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED |
| price | decimal(38,18) | NULLABLE | Limit price |
| size | decimal(38,18) | NOT NULL | Order size |
| filled_size | decimal(38,18) | NOT NULL | Filled amount |
| remaining_size | decimal(38,18) | NOT NULL | Remaining amount |
| avg_fill_price | decimal(38,18) | NOT NULL | Average fill price |
| client_order_id | text | NULLABLE | User-provided ID |
| created_at | timestamptz | NOT NULL | Creation timestamp |
| updated_at | timestamptz | NOT NULL | Update timestamp |
| canceled_at | timestamptz | NULLABLE | Cancellation timestamp |

**Constraints:**
- UNIQUE on `(user_id, market_id, client_order_id)`

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `(market_id, status, created_at)`
- INDEX on `(user_id, status)`

#### order_events
Append-only order event log.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Event ID |
| order_id | uuid | FK(orders.id) | Order reference |
| type | enum | NOT NULL | CREATED, ACCEPTED, PARTIAL_FILL, FILLED, CANCELED, REJECTED, EXPIRED, REPLACED |
| payload | jsonb | NOT NULL | Event data |
| created_at | timestamptz | NOT NULL | Event timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `(order_id, created_at)`

#### trades
Executed trades.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Trade ID |
| market_id | uuid | FK(markets.id) | Market reference |
| taker_order_id | uuid | FK(orders.id) | Taker order |
| maker_order_id | uuid | FK(orders.id) | Maker order |
| taker_user_id | uuid | FK(users.id) | Taker user |
| maker_user_id | uuid | FK(users.id) | Maker user |
| price | decimal(38,18) | NOT NULL | Execution price |
| size | decimal(38,18) | NOT NULL | Trade size (base) |
| quote_amount | decimal(38,18) | NOT NULL | Quote amount |
| maker_fee_amount | decimal(38,18) | NOT NULL | Maker fee |
| taker_fee_amount | decimal(38,18) | NOT NULL | Taker fee |
| fee_asset_id | uuid | FK(assets.id) | Fee asset |
| created_at | timestamptz | NOT NULL | Trade timestamp |

**Constraints:**
- UNIQUE on `(market_id, taker_order_id, maker_order_id, created_at)`

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `(market_id, created_at)`
- INDEX on `taker_user_id`
- INDEX on `maker_user_id`

### LEDGER (DOUBLE-ENTRY) ⭐️

#### ledger_accounts
Double-entry accounts.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Account ID |
| owner_type | enum | NOT NULL | USER, SYSTEM |
| owner_id | uuid | NULLABLE | User ID (NULL for SYSTEM) |
| account_type | enum | NOT NULL | AVAILABLE, LOCKED, FEE, CLEARING, HOT_WALLET, COLD_WALLET, INSURANCE |
| asset_id | uuid | FK(assets.id) | Asset reference |
| created_at | timestamptz | NOT NULL | Creation timestamp |

**Constraints:**
- UNIQUE on `(owner_type, owner_id, account_type, asset_id)`

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `(owner_id, asset_id)`
- INDEX on `account_type`

#### ledger_transactions
Transaction headers (journal entries).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Transaction ID |
| type | enum | NOT NULL | DEPOSIT_CREDIT, WITHDRAWAL_DEBIT, TRADE_SETTLE, FEE_CHARGE, TRANSFER, ADJUSTMENT |
| external_ref | text | NULLABLE | External reference |
| idempotency_key | text | UNIQUE, NULLABLE | Idempotency key |
| metadata | jsonb | NULLABLE | Additional data |
| created_at | timestamptz | NOT NULL | Creation timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- UNIQUE on `idempotency_key`
- INDEX on `(type, created_at)`
- INDEX on `external_ref`

#### ledger_entries
Individual debit/credit lines.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Entry ID |
| ledger_transaction_id | uuid | FK(ledger_transactions.id) | Transaction reference |
| account_id | uuid | FK(ledger_accounts.id) | Account reference |
| direction | enum | NOT NULL | DEBIT, CREDIT |
| amount | decimal(38,18) | NOT NULL | Entry amount (> 0) |
| created_at | timestamptz | NOT NULL | Creation timestamp |

**Invariants:**
- Amount must be > 0
- For each transaction, sum(debits) = sum(credits) per asset
- Entries are immutable (no updates/deletes)

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `(account_id, created_at)`
- INDEX on `ledger_transaction_id`

#### balances_cache
Performance cache of account balances.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| account_id | uuid | PK, FK(ledger_accounts.id) | Account reference |
| available | decimal(38,18) | NOT NULL | Available balance |
| locked | decimal(38,18) | NOT NULL | Locked balance |
| updated_at | timestamptz | NOT NULL | Last update |

**Note:** This is a derived cache. The source of truth is `ledger_entries`.

### DEPOSITS & WITHDRAWALS

#### deposits
Incoming blockchain transactions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Deposit ID |
| user_id | uuid | FK(users.id) | User reference |
| asset_network_id | uuid | FK(asset_networks.id) | Asset/network |
| address | text | NOT NULL | Deposit address |
| tag | text | NULLABLE | Memo/tag |
| txid | text | NOT NULL | Transaction ID |
| amount | decimal(38,18) | NOT NULL | Deposit amount |
| status | enum | NOT NULL | DETECTED, CONFIRMED, CREDITED, REORGED, FAILED |
| detected_at | timestamptz | NULLABLE | Detection time |
| confirmed_at | timestamptz | NULLABLE | Confirmation time |
| credited_at | timestamptz | NULLABLE | Credit time |
| confirmations | int | NOT NULL | Current confirmations |
| block_hash | text | NULLABLE | Block hash |
| block_height | bigint | NULLABLE | Block height |
| source | enum | NOT NULL | BITGO, ANIMICA_NODE, MANUAL |
| provider_event_id | text | UNIQUE, NULLABLE | Provider webhook ID |
| idempotency_key | text | UNIQUE, NULLABLE | Idempotency key |
| raw | jsonb | NULLABLE | Raw webhook data |
| created_at | timestamptz | NOT NULL | Creation timestamp |

**Constraints:**
- UNIQUE on `(asset_network_id, txid, address, COALESCE(tag, ''))`
- UNIQUE on `provider_event_id`
- UNIQUE on `idempotency_key`

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `(user_id, status)`
- INDEX on `(asset_network_id, txid)`
- INDEX on `status`

#### withdrawals
Outgoing blockchain transactions.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Withdrawal ID |
| user_id | uuid | FK(users.id) | User reference |
| asset_network_id | uuid | FK(asset_networks.id) | Asset/network |
| destination_address | text | NOT NULL | Destination address |
| destination_tag | text | NULLABLE | Memo/tag |
| amount | decimal(38,18) | NOT NULL | Withdrawal amount |
| fee_amount | decimal(38,18) | NOT NULL | Fee amount |
| status | enum | NOT NULL | REQUESTED, RISK_REVIEW, APPROVED, SIGNING, BROADCAST, CONFIRMED, FAILED, CANCELED |
| requested_at | timestamptz | NOT NULL | Request time |
| approved_at | timestamptz | NULLABLE | Approval time |
| broadcast_at | timestamptz | NULLABLE | Broadcast time |
| confirmed_at | timestamptz | NULLABLE | Confirmation time |
| txid | text | NULLABLE | Transaction ID |
| provider | enum | NOT NULL | BITGO, ANIMICA_NODE, MANUAL |
| provider_ref | text | NULLABLE | Provider reference |
| idempotency_key | text | UNIQUE, NULLABLE | Idempotency key |
| risk_score | decimal(5,2) | NULLABLE | Risk score (0-100) |
| created_at | timestamptz | NOT NULL | Creation timestamp |
| updated_at | timestamptz | NOT NULL | Update timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `(user_id, status)`
- INDEX on `status`
- INDEX on `asset_network_id`

#### withdrawal_approvals
Multi-signature approval tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Approval ID |
| withdrawal_id | uuid | FK(withdrawals.id) | Withdrawal reference |
| approver_user_id | uuid | FK(users.id) | Approver |
| action | enum | NOT NULL | APPROVE, REJECT |
| note | text | NULLABLE | Approval note |
| created_at | timestamptz | NOT NULL | Approval timestamp |

**Constraints:**
- UNIQUE on `(withdrawal_id, approver_user_id)`

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `withdrawal_id`

### SYSTEM & AUDIT

#### fee_schedules
Fee configuration.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Schedule ID |
| scope | enum | NOT NULL | GLOBAL, USER_TIER, MARKET |
| user_id | uuid | NULLABLE | User-specific fees |
| market_id | uuid | FK(markets.id) | Market-specific fees |
| maker_bps | int | NOT NULL | Maker fee (basis points) |
| taker_bps | int | NOT NULL | Taker fee (basis points) |
| withdrawal_fee_override | decimal(38,18) | NULLABLE | Withdrawal fee override |
| effective_from | timestamptz | NOT NULL | Start date |
| effective_to | timestamptz | NULLABLE | End date |
| created_at | timestamptz | NOT NULL | Creation timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `(scope, effective_from, effective_to)`
- INDEX on `user_id`
- INDEX on `market_id`

#### audit_logs
Comprehensive audit trail.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | uuid | PK | Log ID |
| actor_user_id | uuid | FK(users.id) | Actor (NULL for system) |
| actor_type | enum | NOT NULL | USER, ADMIN, SYSTEM |
| action | text | NOT NULL | Action performed |
| entity_type | text | NOT NULL | Entity type |
| entity_id | uuid | NULLABLE | Entity ID |
| ip | inet | NULLABLE | Source IP |
| user_agent | text | NULLABLE | User agent |
| before | jsonb | NULLABLE | State before |
| after | jsonb | NULLABLE | State after |
| created_at | timestamptz | NOT NULL | Log timestamp |

**Indexes:**
- PRIMARY KEY on `id`
- INDEX on `(actor_user_id, created_at)`
- INDEX on `(entity_type, entity_id)`
- INDEX on `created_at`

#### idempotency_keys
Duplicate prevention.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| key | text | PK | Idempotency key |
| scope | enum | NOT NULL | DEPOSIT, WITHDRAWAL, TRADE_SETTLE, WEBHOOK |
| request_hash | text | NOT NULL | Request hash |
| response | jsonb | NULLABLE | Cached response |
| created_at | timestamptz | NOT NULL | Creation timestamp |
| expires_at | timestamptz | NULLABLE | Expiration time |

**Indexes:**
- PRIMARY KEY on `key`
- INDEX on `(scope, created_at)`
- INDEX on `expires_at`

## Migration Strategy

### Initial Setup

```bash
# 1. Create database
createdb exchange_api

# 2. Set DATABASE_URL
export DATABASE_URL="postgresql://user:pass@localhost/exchange_api"

# 3. Run migrations
cd services/exchange-api
pnpm prisma migrate deploy

# 4. Generate client
pnpm prisma generate
```

### Adding Migrations

```bash
# 1. Edit schema.prisma

# 2. Create migration
pnpm prisma migrate dev --name add_feature_x

# 3. Test locally

# 4. Deploy to production
pnpm prisma migrate deploy
```

## Data Integrity

### Constraints Enforced

1. **Foreign keys** - All references use RESTRICT or CASCADE appropriately
2. **Unique constraints** - Prevent duplicate data
3. **Enums** - Type-safe status values
4. **NOT NULL** - Required fields enforced
5. **DECIMAL(38,18)** - Sufficient precision for crypto amounts

### Application-Level Enforcement

1. **Positive amounts** - Validated before insert
2. **Double-entry balance** - Verified before commit
3. **Sufficient balance** - Checked before debit
4. **Idempotency** - Enforced via unique keys
5. **Immutability** - Ledger entries never updated/deleted

## Performance Considerations

### Indexing Strategy

- **Query patterns** - Indexes on frequently queried columns
- **Foreign keys** - All FKs indexed
- **Time-based queries** - Indexes on `created_at`, `updated_at`
- **Status filters** - Composite indexes on `(entity_id, status, created_at)`

### Scaling Strategy

- **Balance cache** - Avoid real-time ledger calculations
- **Partitioning** - Consider partitioning large tables by date
- **Read replicas** - Use replicas for reporting queries
- **Connection pooling** - Use PgBouncer or similar

## Backup & Recovery

### Backup Strategy

- **Daily full backups** - PostgreSQL pg_dump
- **WAL archiving** - Point-in-time recovery
- **Ledger immutability** - Natural append-only structure

### Recovery Procedure

1. Restore from latest backup
2. Replay WAL to desired point
3. Run reconciliation to verify integrity
4. Rebuild balance caches if needed

## Security

### Access Control

- **Row-level security** - Consider RLS for multi-tenancy
- **Role-based permissions** - Separate read/write roles
- **Audit logging** - All modifications logged
- **Encryption** - Encrypt sensitive columns (PII, keys)

### PII Handling

- Encrypt `email`, `phone`, `legal_name`
- Hash `password_hash`, `secret_hash`
- Mask in logs and error messages
- Comply with GDPR/CCPA requirements
