# Private Authenticated Routes

This directory contains private API routes that require API key authentication.

## Files

### 1. `private_accounts.ts`
Account and balance endpoints for authenticated users.

**Endpoints:**
- `GET /api/v1/account` - Returns user account information
  - Requires scope: `account:read`
  - Returns: user_id, email, status, kyc_status, tier, created_at

- `GET /api/v1/balances` - Returns all user balances across assets
  - Requires scope: `balances:read`
  - Returns: array of { asset, available, locked, total }

### 2. `private_orders.ts`
Order management endpoints for trading.

**Endpoints:**
- `GET /api/v1/orders` - List user orders
  - Requires scope: `orders:read`
  - Query params: market, status, limit, cursor
  - Supports cursor pagination

- `POST /api/v1/orders` - Place new order
  - Requires scope: `orders:write`
  - Body: market, side, type, price, size, client_order_id, time_in_force
  - Supports Idempotency-Key header
  - Validates against market rules (min size, tick size, size step)

- `DELETE /api/v1/orders/:id` - Cancel order
  - Requires scope: `orders:write`
  - Params: order id (UUID)

- `POST /api/v1/orders/:id/replace` - Cancel and replace order
  - Requires scope: `orders:write`
  - Body: price, size, client_order_id (optional)
  - Cancels existing order and places new one atomically

### 3. `private_transfers.ts`
Deposit and withdrawal management endpoints.

**Endpoints:**
- `GET /api/v1/transfers/deposits` - List deposits
  - Requires scope: `transfers:read`
  - Query params: asset, status, limit, cursor
  - Supports cursor pagination

- `GET /api/v1/transfers/withdrawals` - List withdrawals
  - Requires scope: `transfers:read`
  - Query params: asset, status, limit, cursor
  - Supports cursor pagination

- `POST /api/v1/withdrawals` - Create withdrawal request
  - Requires scope: `transfers:write`
  - Body: asset, amount, address, memo, network, two_factor_code
  - Validates sufficient balance
  - Supports Idempotency-Key header

### 4. `auth.ts`
API key management endpoints (admin/internal use).

**Endpoints:**
- `POST /api/v1/auth/api-keys` - Create new API key
  - Requires scope: `admin`
  - Body: name, scopes, ip_allowlist
  - Returns: API key and secret (shown only once)

- `GET /api/v1/auth/api-keys` - List user's API keys
  - Requires scope: `admin`
  - Returns: array of API keys (without secrets)

- `DELETE /api/v1/auth/api-keys/:id` - Revoke API key
  - Requires scope: `admin`
  - Params: API key id (UUID)

## Authentication

All routes require API key authentication using the following headers:
- `X-API-KEY`: Key identifier
- `X-API-TIMESTAMP`: Unix timestamp in milliseconds
- `X-API-NONCE`: Unique nonce
- `X-API-SIGNATURE`: HMAC-SHA256 signature

See `api_key_auth.ts` middleware for implementation details.

## Scopes

Available scopes:
- `account:read` - Read account information
- `balances:read` - Read balances
- `orders:read` - Read orders
- `orders:write` - Place and cancel orders
- `transfers:read` - Read deposits and withdrawals
- `transfers:write` - Create withdrawals
- `admin` - Manage API keys

## Security Features

- **Scope-based authorization**: Each endpoint checks required scopes
- **Audit logging**: Sensitive operations logged to audit_logs table
- **Idempotency**: POST endpoints support Idempotency-Key header
- **Validation**: Request validation using Zod schemas
- **Market rules**: Order validation against market configuration
- **Balance checks**: Withdrawal validation against available balance

## Usage Example

```typescript
import { createPrivateAccountsRouter } from './routes/private_accounts.js';
import { createPrivateOrdersRouter } from './routes/private_orders.js';
import { createPrivateTransfersRouter } from './routes/private_transfers.js';
import { createAuthRouter } from './routes/auth.js';

// Create routers
const accountsRouter = createPrivateAccountsRouter(prisma, config, logger);
const ordersRouter = createPrivateOrdersRouter(prisma, matchingEngineClient, config, logger);
const transfersRouter = createPrivateTransfersRouter(prisma, config, logger);
const authRouter = createAuthRouter(prisma, config, logger);

// Apply API key authentication middleware
app.use('/api/v1/account', apiKeyAuth, accountsRouter);
app.use('/api/v1/orders', apiKeyAuth, ordersRouter);
app.use('/api/v1/transfers', apiKeyAuth, transfersRouter);
app.use('/api/v1/auth', apiKeyAuth, authRouter);
```

## Error Handling

All routes use the standard error handling middleware and return consistent error responses:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Validation failed",
    "details": { ... },
    "request_id": "uuid"
  }
}
```

Error codes:
- `UNAUTHORIZED` (401) - Invalid/missing authentication
- `FORBIDDEN` (403) - Insufficient permissions
- `NOT_FOUND` (404) - Resource not found
- `VALIDATION_ERROR` (400) - Invalid request data
- `CONFLICT` (409) - Resource conflict (e.g., duplicate client_order_id)
- `BAD_REQUEST` (400) - Invalid operation
- `INTERNAL_ERROR` (500) - Server error
