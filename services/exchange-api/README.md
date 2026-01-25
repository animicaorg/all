# Exchange API Service

A production-ready centralized exchange (CEX) API for Animica and multiple blockchain networks, featuring REST and WebSocket APIs, strict double-entry accounting, and comprehensive trading functionality.

## Table of Contents

1. [API Overview](#api-overview)
2. [Getting Started](#getting-started)
3. [Authentication](#authentication)
4. [REST API Endpoints](#rest-api-endpoints)
5. [WebSocket API](#websocket-api)
6. [Rate Limiting](#rate-limiting)
7. [Error Handling](#error-handling)
8. [Code Examples](#code-examples)
9. [Running the Server](#running-the-server)
10. [Configuration](#configuration)
11. [Architecture](#architecture)
12. [Development](#development)

---

## API Overview

The Exchange API provides comprehensive trading and account management capabilities through two complementary interfaces:

- **REST API** - Standard HTTP/JSON endpoints for account management, order placement, and market data queries
- **WebSocket API** - Real-time streaming for order books, trades, tickers, and account updates

### Base URLs

```
REST API:       https://api.example.com/api/v1
WebSocket API:  wss://api.example.com/ws
```

### Key Features

- ✅ Multi-asset trading (BTC, ETH, USDT, ANM, and more)
- ✅ Multiple order types (LIMIT, MARKET) with time-in-force options
- ✅ Real-time market data streaming
- ✅ Secure HMAC-SHA256 API key authentication
- ✅ Three-layer rate limiting
- ✅ Double-entry accounting with strict invariants
- ✅ Comprehensive audit logging

---

## Getting Started

### 1. Create an Account

Contact your exchange administrator or use the web interface to create an account.

### 2. Generate API Keys

Create API keys through the web interface or API:

```bash
POST /api/v1/auth/keys
```

### 3. Make Your First Request

```bash
# Get all markets
curl https://api.example.com/api/v1/markets

# Get your balance (requires authentication)
curl -H "X-API-KEY: your_key_id" \
     -H "X-API-TIMESTAMP: $(date +%s000)" \
     -H "X-API-NONCE: $(uuidgen)" \
     -H "X-API-SIGNATURE: <calculated_signature>" \
     https://api.example.com/api/v1/account/balances
```

---

## Authentication

The API uses **HMAC-SHA256 signature authentication** for secure, stateless request verification.

### API Key Structure

Each API key consists of:
- **Key ID**: 12-character identifier (e.g., `ak_abc123def456`)
- **Secret**: 64-character hex string (keep secure!)
- **Scopes**: Permissions (e.g., `orders:read`, `orders:write`)

### Required Headers

All authenticated requests must include:

| Header | Description | Example |
|--------|-------------|---------|
| `X-API-KEY` | Your API key ID | `ak_abc123def456` |
| `X-API-TIMESTAMP` | Current Unix timestamp (ms) | `1706180000000` |
| `X-API-NONCE` | Unique request identifier | `550e8400-e29b-41d4-a716-446655440000` |
| `X-API-SIGNATURE` | HMAC-SHA256 signature (base64) | `dGVzdHNpZ25hdHVyZQ==` |

### Signature Calculation

1. **Hash the request body**:
   ```javascript
   const bodyHash = crypto.createHash('sha256')
     .update(requestBody || '')
     .digest('hex');
   ```

2. **Build the prehash string**:
   ```
   <timestamp>\n<nonce>\n<METHOD>\n<path>\n<query>\n<bodyHash>
   ```
   
   Example:
   ```
   1706180000000
   550e8400-e29b-41d4-a716-446655440000
   POST
   /api/v1/orders
   
   a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3
   ```

3. **Compute HMAC signature**:
   ```javascript
   const signature = crypto.createHmac('sha256', apiSecret)
     .update(prehash, 'utf8')
     .digest('base64');
   ```

### API Key Scopes

| Scope | Description |
|-------|-------------|
| `account:read` | View account information |
| `balances:read` | View balances |
| `orders:read` | View orders |
| `orders:write` | Place and cancel orders |
| `transfers:read` | View transfers |
| `transfers:write` | Create deposits and withdrawals |
| `admin` | Administrative operations |

---

## REST API Endpoints

### Public Endpoints (No Authentication)

#### GET /api/v1/markets

List all active trading markets.

**Response:**
```json
[
  {
    "market": "BTC-USD",
    "base": "BTC",
    "quote": "USD",
    "price_decimals": 2,
    "size_decimals": 8,
    "min_order_size": "0.0001",
    "status": "ACTIVE"
  }
]
```

---

#### GET /api/v1/orderbook

Get order book depth for a market.

**Query Parameters:**
- `market` (required): Market symbol (e.g., `BTC-USD`)
- `depth` (optional): Number of levels (default: 20, max: 50)

**Response:**
```json
{
  "market": "BTC-USD",
  "sequence": 12345,
  "bids": [
    ["50000.00", "1.5"],
    ["49999.00", "2.0"]
  ],
  "asks": [
    ["50001.00", "1.2"],
    ["50002.00", "3.0"]
  ],
  "timestamp": "2024-01-25T12:00:00.000Z"
}
```

---

#### GET /api/v1/trades

Get recent trades for a market.

**Query Parameters:**
- `market` (required): Market symbol
- `limit` (optional): Number of trades (default: 50, max: 100)

**Response:**
```json
[
  {
    "trade_id": "trade_abc123",
    "market": "BTC-USD",
    "price": "50000.00",
    "size": "0.5",
    "side": "buy",
    "timestamp": "2024-01-25T12:00:00.000Z"
  }
]
```

---

#### GET /api/v1/tickers

Get ticker information for markets.

**Query Parameters:**
- `market` (optional): Specific market (omit for all markets)

**Response:**
```json
{
  "BTC-USD": {
    "market": "BTC-USD",
    "last": "50000.00",
    "bid": "49999.00",
    "ask": "50001.00",
    "volume_24h": "1234.56",
    "high_24h": "51000.00",
    "low_24h": "49000.00",
    "change_24h": "2.5",
    "timestamp": "2024-01-25T12:00:00.000Z"
  }
}
```

---

#### GET /api/v1/candles

Get OHLCV candle data.

**Query Parameters:**
- `market` (required): Market symbol
- `interval` (required): Time interval (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`)
- `start` (optional): Start timestamp (ISO 8601)
- `end` (optional): End timestamp (ISO 8601)
- `limit` (optional): Number of candles (default: 100, max: 500)

**Response:**
```json
[
  {
    "timestamp": "2024-01-25T12:00:00.000Z",
    "open": "50000.00",
    "high": "50500.00",
    "low": "49800.00",
    "close": "50200.00",
    "volume": "123.45"
  }
]
```

---

### Private Endpoints (Authentication Required)

#### GET /api/v1/account

Get account information.

**Scopes:** `account:read`

**Response:**
```json
{
  "user_id": "usr_abc123",
  "email": "user@example.com",
  "status": "ACTIVE",
  "kyc_status": "APPROVED",
  "created_at": "2024-01-01T00:00:00.000Z"
}
```

---

#### GET /api/v1/account/balances

Get all account balances.

**Scopes:** `balances:read`

**Response:**
```json
[
  {
    "asset": "BTC",
    "available": "1.5",
    "locked": "0.5",
    "total": "2.0"
  },
  {
    "asset": "USD",
    "available": "10000.00",
    "locked": "5000.00",
    "total": "15000.00"
  }
]
```

---

#### GET /api/v1/orders

List user orders.

**Scopes:** `orders:read`

**Query Parameters:**
- `market` (optional): Filter by market
- `status` (optional): Filter by status (`OPEN`, `FILLED`, `CANCELED`, etc.)
- `limit` (optional): Results per page (default: 50, max: 100)
- `cursor` (optional): Pagination cursor

**Response:**
```json
{
  "data": [
    {
      "order_id": "ord_abc123",
      "market": "BTC-USD",
      "side": "BUY",
      "type": "LIMIT",
      "status": "OPEN",
      "price": "50000.00",
      "size": "0.5",
      "filled_size": "0.0",
      "remaining_size": "0.5",
      "avg_fill_price": "0.00",
      "client_order_id": "my-order-1",
      "created_at": "2024-01-25T12:00:00.000Z",
      "updated_at": "2024-01-25T12:00:00.000Z"
    }
  ],
  "pagination": {
    "cursor": "next_page_token",
    "has_more": true
  }
}
```

---

#### POST /api/v1/orders

Place a new order.

**Scopes:** `orders:write`

**Request Body:**
```json
{
  "market": "BTC-USD",
  "side": "BUY",
  "type": "LIMIT",
  "price": "50000.00",
  "size": "0.5",
  "time_in_force": "GTC",
  "client_order_id": "my-order-1"
}
```

**Response:**
```json
{
  "order_id": "ord_abc123",
  "status": "OPEN",
  "message": "Order placed successfully"
}
```

**Order Types:**
- `LIMIT`: Order with specified price
- `MARKET`: Order at best available price

**Time in Force:**
- `GTC` (Good-Till-Cancel): Remains active until filled or canceled
- `IOC` (Immediate-or-Cancel): Fill immediately or cancel
- `FOK` (Fill-or-Kill): Fill completely or cancel

---

#### DELETE /api/v1/orders/:id

Cancel an order.

**Scopes:** `orders:write`

**Response:**
```json
{
  "order_id": "ord_abc123",
  "status": "CANCELED",
  "message": "Order canceled successfully"
}
```

---

#### POST /api/v1/orders/:id/replace

Replace an existing order (atomic cancel + place).

**Scopes:** `orders:write`

**Request Body:**
```json
{
  "price": "50100.00",
  "size": "0.6"
}
```

**Response:**
```json
{
  "old_order_id": "ord_abc123",
  "new_order_id": "ord_def456",
  "status": "OPEN"
}
```

---

#### GET /api/v1/deposits

List deposit history.

**Scopes:** `transfers:read`

**Query Parameters:**
- `asset` (optional): Filter by asset
- `status` (optional): Filter by status
- `limit` (optional): Results per page
- `cursor` (optional): Pagination cursor

**Response:**
```json
{
  "data": [
    {
      "deposit_id": "dep_abc123",
      "asset": "BTC",
      "amount": "1.5",
      "network": "Bitcoin",
      "address": "bc1q...",
      "txid": "abc123...",
      "confirmations": 6,
      "status": "COMPLETED",
      "created_at": "2024-01-25T12:00:00.000Z"
    }
  ],
  "pagination": {
    "cursor": "next_page_token",
    "has_more": false
  }
}
```

---

#### GET /api/v1/withdrawals

List withdrawal history.

**Scopes:** `transfers:read`

**Response:** Similar to deposits endpoint.

---

#### POST /api/v1/withdrawals

Request a withdrawal.

**Scopes:** `transfers:write`

**Request Body:**
```json
{
  "asset": "BTC",
  "amount": "1.5",
  "network": "Bitcoin",
  "address": "bc1q...",
  "tag": "",
  "note": "Withdrawal to cold storage"
}
```

**Response:**
```json
{
  "withdrawal_id": "wth_abc123",
  "status": "PENDING",
  "message": "Withdrawal request submitted"
}
```

---

#### POST /api/v1/auth/keys

Create a new API key.

**Scopes:** `account:read`

**Request Body:**
```json
{
  "name": "Trading Bot",
  "scopes": ["orders:read", "orders:write", "balances:read"],
  "ip_allowlist": ["203.0.113.1", "203.0.113.2"]
}
```

**Response:**
```json
{
  "id": "key_abc123",
  "name": "Trading Bot",
  "key_id": "ak_abc123def456",
  "secret": "64-character-hex-secret",
  "scopes": ["orders:read", "orders:write", "balances:read"],
  "ip_allowlist": ["203.0.113.1", "203.0.113.2"],
  "created_at": "2024-01-25T12:00:00.000Z",
  "warning": "Save this secret securely. It will not be shown again."
}
```

---

#### GET /api/v1/auth/keys

List all API keys for the authenticated user.

**Scopes:** `account:read`

**Response:**
```json
[
  {
    "id": "key_abc123",
    "name": "Trading Bot",
    "key_id": "ak_abc123def456",
    "scopes": ["orders:read", "orders:write"],
    "ip_allowlist": null,
    "created_at": "2024-01-25T12:00:00.000Z",
    "last_used_at": "2024-01-26T08:30:00.000Z"
  }
]
```

---

#### DELETE /api/v1/auth/keys/:id

Revoke an API key.

**Scopes:** `account:read`

**Response:**
```json
{
  "message": "API key revoked successfully"
}
```

---

## WebSocket API

The WebSocket API provides real-time streaming for market data and account updates.

### Connection

```javascript
const ws = new WebSocket('wss://api.example.com/ws');
```

### Authentication

Send authentication message immediately after connection:

```json
{
  "op": "auth",
  "apiKey": "ak_abc123def456",
  "timestamp": 1706180000000,
  "nonce": "unique-nonce-123",
  "signature": "base64-hmac-signature"
}
```

### Subscribing to Channels

```json
{
  "op": "subscribe",
  "id": 1,
  "channels": [
    { "name": "book", "market": "BTC-USD" },
    { "name": "trades", "market": "BTC-USD" },
    { "name": "tickers", "market": "ETH-USD" }
  ]
}
```

### Available Channels

#### Public Channels (no auth required)
- `book` - Order book updates (requires `market`)
- `trades` - Trade executions (requires `market`)
- `tickers` - Ticker updates (requires `market`)
- `candles` - OHLCV candles (requires `market` and `interval`)

#### Private Channels (authentication required)
- `orders` - User order updates
- `balances` - User balance updates

### Message Format

**Snapshot:**
```json
{
  "type": "snapshot",
  "channel": "book",
  "market": "BTC-USD",
  "seq": 12345,
  "bids": [["50000.00", "1.5"]],
  "asks": [["50001.00", "1.2"]],
  "ts": 1706180000000
}
```

**Update:**
```json
{
  "type": "update",
  "channel": "book",
  "market": "BTC-USD",
  "seq": 12346,
  "changes": {
    "bids": [["50000.00", "2.0"]],
    "asks": [["50001.00", "0"]]
  },
  "ts": 1706180000100
}
```

### Full Documentation

See [WebSocket API Documentation](src/ws/README.md) for complete protocol specification.

---

## Rate Limiting

The API implements three layers of rate limiting for security and performance:

### 1. Public Endpoints (IP-based)

- **Limit:** 120 requests per minute per IP
- **Window:** 60 seconds rolling
- **Applies to:** All unauthenticated endpoints

### 2. Private Endpoints (API Key-based)

- **Limit:** 60 requests per minute per API key
- **Burst:** 20 requests instant burst
- **Window:** 60 seconds rolling
- **Applies to:** All authenticated endpoints

### 3. User Aggregate

- **Limit:** 240 requests per minute per user (across all API keys)
- **Window:** 60 seconds rolling
- **Purpose:** Prevent abuse from multiple API keys

### Rate Limit Headers

All responses include rate limit information:

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 2024-01-25T12:01:00.000Z
Retry-After: 15
```

### Rate Limit Exceeded

When rate limited, you'll receive a `429 Too Many Requests` response:

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Rate limit exceeded. Please retry after 15 seconds.",
    "request_id": "req_abc123"
  }
}
```

---

## Error Handling

All errors follow a consistent format:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable error description",
    "request_id": "req_abc123",
    "details": {}
  }
}
```

### HTTP Status Codes

| Code | Description |
|------|-------------|
| `200` | Success |
| `400` | Bad Request - Invalid parameters |
| `401` | Unauthorized - Authentication failed |
| `403` | Forbidden - Insufficient permissions |
| `404` | Not Found - Resource doesn't exist |
| `409` | Conflict - Resource state conflict |
| `429` | Too Many Requests - Rate limit exceeded |
| `500` | Internal Server Error |
| `503` | Service Unavailable |

### Common Error Codes

| Code | Description |
|------|-------------|
| `UNAUTHORIZED` | Authentication failed or missing |
| `FORBIDDEN` | Insufficient scope/permissions |
| `VALIDATION_ERROR` | Invalid request parameters |
| `NOT_FOUND` | Resource not found |
| `INSUFFICIENT_BALANCE` | Not enough funds |
| `ORDER_NOT_FOUND` | Order doesn't exist or not owned |
| `MARKET_NOT_FOUND` | Market doesn't exist or inactive |
| `RATE_LIMIT_EXCEEDED` | Too many requests |
| `INTERNAL_ERROR` | Server error |

---

## Code Examples

### cURL Examples

#### Get Markets
```bash
curl https://api.example.com/api/v1/markets
```

#### Place Order
```bash
curl -X POST https://api.example.com/api/v1/orders \
  -H "X-API-KEY: ak_abc123def456" \
  -H "X-API-TIMESTAMP: 1706180000000" \
  -H "X-API-NONCE: $(uuidgen)" \
  -H "X-API-SIGNATURE: <calculated>" \
  -H "Content-Type: application/json" \
  -d '{
    "market": "BTC-USD",
    "side": "BUY",
    "type": "LIMIT",
    "price": "50000.00",
    "size": "0.5"
  }'
```

### TypeScript/Node.js Client

```typescript
import crypto from 'crypto';
import { v4 as uuidv4 } from 'uuid';

class ExchangeClient {
  constructor(
    private apiKeyId: string,
    private apiSecret: string,
    private baseUrl: string = 'https://api.example.com/api/v1'
  ) {}

  private signRequest(method: string, path: string, query: string, body: string): Record<string, string> {
    const timestamp = Date.now().toString();
    const nonce = uuidv4();
    const bodyHash = crypto.createHash('sha256').update(body).digest('hex');
    
    const prehash = [timestamp, nonce, method, path, query, bodyHash].join('\n');
    const signature = crypto
      .createHmac('sha256', this.apiSecret)
      .update(prehash, 'utf8')
      .digest('base64');

    return {
      'X-API-KEY': this.apiKeyId,
      'X-API-TIMESTAMP': timestamp,
      'X-API-NONCE': nonce,
      'X-API-SIGNATURE': signature,
      'Content-Type': 'application/json',
    };
  }

  async getBalances() {
    const headers = this.signRequest('GET', '/account/balances', '', '');
    const response = await fetch(`${this.baseUrl}/account/balances`, { headers });
    return response.json();
  }

  async placeOrder(order: any) {
    const body = JSON.stringify(order);
    const headers = this.signRequest('POST', '/orders', '', body);
    const response = await fetch(`${this.baseUrl}/orders`, {
      method: 'POST',
      headers,
      body,
    });
    return response.json();
  }
}

// Usage
const client = new ExchangeClient('ak_abc123def456', 'your-secret-key');

// Get balances
const balances = await client.getBalances();
console.log(balances);

// Place order
const order = await client.placeOrder({
  market: 'BTC-USD',
  side: 'BUY',
  type: 'LIMIT',
  price: '50000.00',
  size: '0.5',
});
console.log(order);
```

### Python Client

```python
import hashlib
import hmac
import base64
import time
import uuid
import json
import requests

class ExchangeClient:
    def __init__(self, api_key_id, api_secret, base_url='https://api.example.com/api/v1'):
        self.api_key_id = api_key_id
        self.api_secret = api_secret
        self.base_url = base_url

    def _sign_request(self, method, path, query='', body=''):
        timestamp = str(int(time.time() * 1000))
        nonce = str(uuid.uuid4())
        body_hash = hashlib.sha256(body.encode('utf-8')).hexdigest()
        
        prehash = '\n'.join([timestamp, nonce, method, path, query, body_hash])
        signature = base64.b64encode(
            hmac.new(
                self.api_secret.encode('utf-8'),
                prehash.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')

        return {
            'X-API-KEY': self.api_key_id,
            'X-API-TIMESTAMP': timestamp,
            'X-API-NONCE': nonce,
            'X-API-SIGNATURE': signature,
            'Content-Type': 'application/json',
        }

    def get_balances(self):
        headers = self._sign_request('GET', '/account/balances')
        response = requests.get(f'{self.base_url}/account/balances', headers=headers)
        return response.json()

    def place_order(self, order):
        body = json.dumps(order)
        headers = self._sign_request('POST', '/orders', body=body)
        response = requests.post(f'{self.base_url}/orders', headers=headers, data=body)
        return response.json()

# Usage
client = ExchangeClient('ak_abc123def456', 'your-secret-key')

# Get balances
balances = client.get_balances()
print(balances)

# Place order
order = client.place_order({
    'market': 'BTC-USD',
    'side': 'BUY',
    'type': 'LIMIT',
    'price': '50000.00',
    'size': '0.5',
})
print(order)
```

---

## Running the Server

### HTTP Server

```bash
cd services/exchange-api

# Install dependencies
pnpm install

# Setup database
pnpm db:migrate

# Start HTTP server
pnpm start:http
# Server listening on http://0.0.0.0:3000
```

### WebSocket Server

```bash
# Start WebSocket server
pnpm start:ws
# WebSocket server listening on ws://0.0.0.0:3001
```

### Both Servers

```bash
# Start both HTTP and WebSocket servers
pnpm start
```

### Development Mode

```bash
# Start with auto-reload
pnpm dev
```

### Docker

```bash
# Build image
docker build -t exchange-api .

# Run container
docker run -p 3000:3000 -p 3001:3001 \
  -e DATABASE_URL="postgresql://..." \
  exchange-api
```

---

## Configuration

Configure the service via environment variables. Copy `.env.example` to `.env` and customize:

### Service Configuration

```env
NODE_ENV=production
SERVICE_NAME=exchange-api
LOG_LEVEL=info
```

### Server Ports

```env
HTTP_PORT=3000
HTTP_HOST=0.0.0.0
WS_PORT=3001
WS_HOST=0.0.0.0
```

### Database

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/exchange_api
```

### Redis (for rate limiting and caching)

```env
REDIS_URL=redis://localhost:6379
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0
```

### Authentication

```env
# Timestamp window for signature validation (±30s default)
API_KEY_TIMESTAMP_WINDOW_MS=30000

# Nonce TTL to prevent replay attacks (5 minutes)
API_KEY_NONCE_TTL_MS=300000

# JWT for session tokens (if using web sessions)
JWT_SECRET=your-secret-key
JWT_EXPIRES_IN=24h
```

### Rate Limiting

```env
# Public endpoints (per IP)
RATE_LIMIT_PUBLIC_PER_IP=120
RATE_LIMIT_PUBLIC_WINDOW_MS=60000

# Private endpoints (per API key)
RATE_LIMIT_PRIVATE_PER_KEY=60
RATE_LIMIT_PRIVATE_WINDOW_MS=60000
RATE_LIMIT_PRIVATE_BURST=20

# User aggregate (across all keys)
RATE_LIMIT_USER_AGGREGATE=240
```

### CORS

```env
CORS_ORIGIN=*
CORS_CREDENTIALS=false
```

### Cache TTLs

```env
CACHE_ORDERBOOK_TTL_MS=250
CACHE_TICKER_TTL_MS=1000
CACHE_MARKETS_TTL_MS=60000
```

### Pagination

```env
MAX_PAGE_SIZE=100
DEFAULT_PAGE_SIZE=50
```

### Market Data

```env
ORDERBOOK_MAX_DEPTH=50
TRADES_MAX_LIMIT=100
```

### WebSocket

```env
WS_HEARTBEAT_INTERVAL_MS=15000
WS_HEARTBEAT_TIMEOUT_MS=45000
WS_MAX_SUBSCRIPTIONS_PER_CLIENT=50
WS_MAX_OUTGOING_QUEUE_SIZE=1000
```

---

## Architecture

This service provides the core data model and business logic for a cryptocurrency exchange, supporting:

- Multiple assets and networks (Bitcoin, Ethereum, Animica, etc.)
- BitGo-managed and Animica-native wallets
- Strict double-entry accounting ledger
- Order book trading with multiple order types
- Deposit and withdrawal processing
- KYC/AML compliance tracking
- Comprehensive audit logging
- Idempotency for all external events

## Core Principles

### 1. Double-Entry Accounting

The ledger follows strict double-entry accounting principles:

- **Every transaction must balance**: For each asset, total debits = total credits
- **No magic balances**: Balances are derived from the ledger, not stored separately
- **Immutable entries**: Ledger entries can never be updated or deleted
- **Audit trail**: Every transaction has a clear external reference

### 2. Data Model

#### Users & Authentication
- `users`: Core user accounts with email/phone and status
- `user_profiles`: Extended profile information
- `api_keys`: API key management with scopes and IP allowlists
- `sessions`: Session management for web/mobile clients

#### KYC/AML
- `kyc_cases`: KYC verification workflows
- `kyc_documents`: Document storage references

#### Assets & Networks
- `networks`: Blockchain networks (BTC, ETH, Animica, etc.)
- `assets`: Tradable assets (BTC, ETH, USDT, ANM, etc.)
- `asset_networks`: Asset deployment per network (e.g., USDT on Ethereum)
- `wallets`: House wallets for custody
- `user_deposit_addresses`: Per-user deposit addresses

#### Markets & Trading
- `markets`: Trading pairs (ANM-USD, BTC-USDT, etc.)
- `orders`: User orders with lifecycle tracking
- `order_events`: Append-only order event log
- `trades`: Executed trades with fees

#### Ledger (Core)
- `ledger_accounts`: Double-entry accounts (AVAILABLE, LOCKED, FEE, etc.)
- `ledger_transactions`: Transaction headers (journal entries)
- `ledger_entries`: Individual debit/credit entries
- `balances_cache`: Performance cache (derived from ledger)

#### Deposits & Withdrawals
- `deposits`: Incoming blockchain transactions
- `withdrawals`: Outgoing transactions with approval workflow
- `withdrawal_approvals`: Multi-signature approval tracking

#### Fees & Audit
- `fee_schedules`: Configurable fee structures
- `audit_logs`: Comprehensive audit trail
- `idempotency_keys`: Prevent duplicate processing

## Invariants

The system enforces these invariants at all times:

1. **DOUBLE-ENTRY BALANCE**: For any ledger transaction, debits = credits per asset
2. **IMMUTABILITY**: Ledger entries are never updated or deleted
3. **POSITIVE AMOUNTS**: All entry amounts must be > 0
4. **ACCOUNT CONSISTENCY**: Balances from ledger must match cache
5. **NO NEGATIVE BALANCES**: User available balances cannot go negative
6. **FUND LOCKING**: Orders lock sufficient funds before acceptance
7. **TRADE SETTLEMENT**: Trades transfer exact amounts with correct fees
8. **IDEMPOTENCY**: External events are processed exactly once
9. **ATOMIC OPERATIONS**: All ledger operations are atomic
10. **AUDIT TRAIL**: All transactions have external references

## Double-Entry Rules

### Deposit Processing

When a deposit is confirmed:
```
DEBIT:  SYSTEM:CLEARING (asset)    amount
CREDIT: USER:AVAILABLE (asset)     amount
```

### Order Placement

When placing a BUY order:
```
DEBIT:  USER:AVAILABLE (quote)     price * size + fees
CREDIT: USER:LOCKED (quote)        price * size + fees
```

When placing a SELL order:
```
DEBIT:  USER:AVAILABLE (base)      size
CREDIT: USER:LOCKED (base)         size
```

### Trade Settlement

When a trade executes:
```
// Base asset transfer (seller -> buyer)
DEBIT:  SELLER:LOCKED (base)       size
CREDIT: BUYER:AVAILABLE (base)     size

// Quote asset transfer (buyer -> seller, minus fees)
DEBIT:  BUYER:LOCKED (quote)       price * size
CREDIT: SELLER:AVAILABLE (quote)   price * size - seller_fee
CREDIT: SYSTEM:FEE (quote)         buyer_fee + seller_fee
```

### Withdrawal Processing

On withdrawal request:
```
DEBIT:  USER:AVAILABLE (asset)     amount + fee
CREDIT: USER:LOCKED (asset)        amount + fee
```

On broadcast:
```
DEBIT:  USER:LOCKED (asset)        amount + fee
CREDIT: SYSTEM:HOT_WALLET (asset)  amount + fee
```

On failure/cancellation:
```
DEBIT:  USER:LOCKED (asset)        amount + fee
CREDIT: USER:AVAILABLE (asset)     amount + fee
```

## Setup

### Prerequisites

- Node.js >= 18.17
- PostgreSQL >= 14
- pnpm >= 9.0.0

### Installation

```bash
cd services/exchange-api
pnpm install
```

### Database Setup

1. Create a PostgreSQL database:
```bash
createdb exchange_api
```

2. Configure environment:
```bash
cp .env.example .env
# Edit .env and set DATABASE_URL
```

3. Run migrations:
```bash
pnpm db:migrate
```

4. Generate Prisma client:
```bash
pnpm db:generate
```

## Development

### Running Tests

```bash
# Run all tests
pnpm test

# Run tests in watch mode
pnpm test:watch

# Run tests with coverage
pnpm test -- --coverage
```

### Database Management

```bash
# Create a new migration
pnpm db:migrate

# Deploy migrations (production)
pnpm db:migrate:deploy

# Push schema changes (development only)
pnpm db:push

# Open Prisma Studio (database GUI)
pnpm db:studio
```

### Code Quality

```bash
# Lint code
pnpm lint

# Build TypeScript
pnpm build
```

## Usage

### Basic Ledger Operations

```typescript
import { prisma } from './src/db/client.js';
import { LedgerService } from './src/services/ledger.js';

const ledger = new LedgerService(prisma);

// Credit a deposit
await ledger.creditDeposit(
  userId,
  assetId,
  '100.50',
  'txid-abc123',
  'unique-idempotency-key'
);

// Lock funds for an order
await ledger.lockFunds(
  userId,
  assetId,
  '50.25',
  'order-xyz789'
);

// Settle a trade
await ledger.settleTrade({
  buyerUserId,
  sellerUserId,
  baseAssetId,
  quoteAssetId,
  baseAmount: '0.5',
  quoteAmount: '5000',
  buyerFee: '15',
  sellerFee: '7.50',
  tradeId: 'trade-123',
});
```

### Reconciliation

```typescript
import { ReconciliationService } from './src/services/reconciliation.js';

const reconciliation = new ReconciliationService(prisma, ledger);

// Reconcile all balances
const result = await reconciliation.reconcileAllBalances();

if (result.mismatches.length > 0) {
  console.error('Balance mismatches detected:', result.mismatches);
  // Alert operations team
}

// Rebuild balance caches from ledger
await reconciliation.rebuildBalanceCaches();
```

## Extending the Model

### Adding a New Asset

1. Insert into `assets` table
2. Add network mappings in `asset_networks`
3. Configure deposit/withdrawal parameters
4. Ledger accounts are created automatically on first use

### Adding a New Market

1. Ensure base and quote assets exist
2. Insert into `markets` table with trading parameters
3. Configure fee schedule in `fee_schedules`
4. Market is ready for order placement

### Adding a New Network

1. Insert into `networks` table
2. Configure chain parameters (confirmations, RPC URL, etc.)
3. Set up wallets in `wallets` table
4. Map assets to network in `asset_networks`

## Security Considerations

### Database Constraints

- All foreign keys use `RESTRICT` or `CASCADE` appropriately
- Unique constraints prevent duplicate deposits/withdrawals
- Check constraints enforce positive amounts
- Enum types enforce valid states

### Code-Level Guards

- Ledger service validates balance before operations
- All transactions use `SERIALIZABLE` isolation level
- Idempotency keys prevent double-processing
- Amount validation enforces positive values

### Audit Trail

- Every operation logged in `audit_logs`
- Ledger entries are immutable
- All transactions have external references
- User actions tracked with IP and user agent

## Monitoring & Alerts

### Daily Reconciliation

Run automated reconciliation daily:

```bash
node scripts/daily-reconciliation.js
```

Alert if:
- Balance mismatches detected
- Unbalanced transactions found
- Ledger immutability violations

### Performance Metrics

Monitor:
- Balance cache hit rate
- Transaction processing time
- Database connection pool utilization
- Failed transaction rate

## Disaster Recovery

### Balance Reconstruction

If balance caches become corrupted:

```typescript
await reconciliation.rebuildBalanceCaches();
```

All balances are recalculated from immutable ledger entries.

### Audit Trail

The ledger provides a complete audit trail:
- All transactions are immutable
- Every change has a timestamp
- External references link to source events
- Cryptographic proofs available for blockchain events

## Future Enhancements

- [ ] Add database triggers for ledger immutability enforcement
- [ ] Implement real-time balance reconciliation
- [ ] Add support for margin trading
- [ ] Implement stop-loss orders
- [ ] Add support for recurring buys/sells
- [ ] Implement maker/taker rebates
- [ ] Add support for staking/earning products
- [ ] Implement cross-chain atomic swaps

## Contributing

See main repository [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## License

Apache-2.0 - See [LICENSE](../../LICENSE.txt)
