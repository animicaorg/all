# Animica Marketplace - Deployment & Integration Guide

## Overview

The Animica Marketplace is a comprehensive token sales platform integrating:
- **Python RPC Backend** - JSON-RPC methods for treasury/pricing data
- **React Explorer Frontend** - Real-time marketplace visualization
- **Flutter Wallet App** - Token purchasing and management
- **Smart Contracts** - On-chain treasury state management
- **Payment Processing** - Stripe & PayPal integration

This guide covers deployment, configuration, and testing of the complete system.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Flutter Wallet App                       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Buy ANM Page → RPC Client → Payment Processor        │  │
│  │ History Page → RPC Methods → Treasury Data           │  │
│  │ Deep Links ←→ Explorer Web App                       │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │ JSON-RPC 2.0
                           │ Deep Links (animica://)
                           ↓
┌─────────────────────────────────────────────────────────────┐
│              Python RPC Backend (Port 8545)                 │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ RPC Methods:                                         │  │
│  │ • explorer_getTreasurySnapshot()                     │  │
│  │ • explorer_getMarketData(token)                      │  │
│  │ • explorer_getPriceHistory(token, days)              │  │
│  │ • wallet_getPurchaseHistory(address, limit, offset)  │  │
│  │ • marketplace_getPricingCurve()                      │  │
│  │ • marketplace_calculatePrice(...)                    │  │
│  │ • marketplace_processStripeWebhook(body, sig)        │  │
│  │ • marketplace_processPayPalWebhook(body, sig)        │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ↓                  ↓                   ↓
   ┌────────────┐  ┌──────────────┐  ┌──────────────┐
   │  Explorer  │  │   Treasury   │  │ Consensus/  │
   │  Database  │  │  Contract    │  │ Execution   │
   └────────────┘  │  (on-chain)  │  │  Layer      │
                   └──────────────┘  └──────────────┘
                           ↑
                    ┌──────────────────┐
                    │ Payment Webhook  │
                    │ Processor        │
                    │ (Stripe/PayPal)  │
                    └──────────────────┘
                           ↑
                           │ POST /webhooks/payment
┌─────────────────────────────────────────────────────────────┐
│              Stripe / PayPal Sandbox Environment             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Payment Processor Webhooks                           │  │
│  │ payment_intent.succeeded                             │  │
│  │ PAYMENT.CAPTURE.COMPLETED                            │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Prerequisites

### System Requirements
- Python 3.9+
- Node.js 18+ (for explorer-web)
- Flutter 3.24+ (for wallet)
- Docker (optional, for containerized deployment)
- PostgreSQL or SQLite (for state database)

### API Keys & Credentials

You'll need credentials from:
1. **Stripe** (for credit card processing)
   - Publishable Key: `pk_test_...` or `pk_live_...`
   - Secret Key: `sk_test_...` or `sk_live_...`
   - Webhook Secret: `whsec_...`

2. **PayPal** (for PayPal processing)
   - Client ID: OAuth2 client ID
   - Client Secret: OAuth2 client secret
   - Webhook ID: For receiving webhooks

3. **CoinGecko** (optional, for market data)
   - API Key: For higher rate limits (free tier available)

## Deployment Steps

### Step 1: Python Backend Setup

#### 1a. Install Dependencies

```bash
# Clone repo (if not already done)
git clone https://github.com/animica/animica.git
cd animica

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -U pip setuptools wheel
pip install -e ".[dev]"
```

#### 1b. Configure Environment

Create `.env` file in project root:

```bash
# RPC Server
RPC_HTTP=http://127.0.0.1:8545
RPC_WS=ws://127.0.0.1:8546
RPC_BIND=127.0.0.1:8545

# Payment Processing
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...

PAYPAL_CLIENT_ID=your_client_id
PAYPAL_CLIENT_SECRET=your_client_secret
PAYPAL_WEBHOOK_ID=your_webhook_id
PAYPAL_SANDBOX=true  # Use sandbox for testing

# Market Data
COINGECKO_API_KEY=your_api_key  # Optional

# Database
DATABASE_URL=sqlite:///./marketplace.db
# Or PostgreSQL:
# DATABASE_URL=postgresql://user:password@localhost/animica_marketplace

# Logging
LOG_LEVEL=INFO
DEBUG=false
```

#### 1c. Initialize Database

```bash
# Create database schema
python -m alembic upgrade head

# Or manually create SQLite database
sqlite3 marketplace.db < schema.sql
```

#### 1d. Start RPC Server

```bash
# Start JSON-RPC server with marketplace methods
python -m rpc.server --config rpc.yml

# Server should be available at http://127.0.0.1:8545
# Test with:
curl -X POST http://127.0.0.1:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "explorer_getTreasurySnapshot",
    "params": [],
    "id": 1
  }'
```

### Step 2: React Explorer Frontend Setup

#### 2a. Install Dependencies

```bash
cd explorer-web
npm install  # or pnpm install

# Verify dependencies
npm list react react-router-dom zustand
```

#### 2b. Configure Environment

Create `.env.local`:

```
VITE_RPC_URL=http://127.0.0.1:8545
VITE_EXPLORER_API=http://127.0.0.1:8080
VITE_MARKETPLACE_ENABLED=true
VITE_ENV=development
```

#### 2c. Build & Run

```bash
# Development
npm run dev
# Marketplace page: http://localhost:5173/marketplace

# Production build
npm run build
npm run preview
```

### Step 3: Flutter Wallet Setup

#### 3a. Install Dependencies

```bash
cd wallet
flutter pub get
flutter pub upgrade

# Verify integration
flutter doctor
```

#### 3b. Configure Environment

Create `lib/services/env.dart`:

```dart
class Env {
  static const rpcUrl = 'http://127.0.0.1:8545';
  static const marketplaceRpcUrl = 'http://127.0.0.1:8545';
  static const explorerUrl = 'https://explorer.animica.io';
  static const stripePk = 'pk_test_...';
  static const paypalClientId = 'your_client_id';
}
```

#### 3c. Build & Run

```bash
# Development
flutter run -d chrome  # Or your target device

# Build for release
flutter build apk      # Android
flutter build ios      # iOS
flutter build web      # Web
```

### Step 4: Smart Contract Deployment

#### 4a. Compile Treasury Contract

```bash
# Compile to IR
python -m vm_py.cli.compile \
  --manifest contracts/examples/treasury/manifest.json \
  --out-dir contracts/build/treasury

# Output: contracts/build/treasury/treasury.ir
```

#### 4b. Deploy to Chain

```bash
# Deploy contract
python contracts/examples/treasury/deploy_and_test.py \
  --rpc http://127.0.0.1:8545 \
  --owner 0x... \
  --supply 1000000000000000000000000000 \
  --target 1000000000000000000000000000
```

### Step 5: Payment Webhook Setup

#### 5a. Create Webhook Endpoint

Your backend needs a POST endpoint for payment webhooks:

```python
# rpc/methods/payments_handler.py
@app.post("/webhooks/payment/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhooks"""
    body = await request.json()
    signature = request.headers.get("stripe-signature")
    
    result = await process_stripe_webhook(
        body=body,
        signature=signature,
        state_db=db,
        policy_provider=policy,
        stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET"),
    )
    
    return {"success": result.success}
```

#### 5b. Configure Webhook in Stripe

1. Go to Stripe Dashboard → Webhooks
2. Add endpoint: `https://your-domain.com/webhooks/payment/stripe`
3. Select events: `payment_intent.succeeded`
4. Copy webhook signing secret to `.env`

#### 5c. Configure Webhook in PayPal

1. Go to PayPal Developer Dashboard → Webhooks
2. Create webhook: `https://your-domain.com/webhooks/payment/paypal`
3. Select events: `PAYMENT.CAPTURE.COMPLETED`
4. Copy webhook ID to `.env`

## Testing

### Integration Testing

```bash
# Run integration tests
pytest tests/integration/test_marketplace_e2e.py -v

# Run with coverage
pytest --cov=rpc.methods.marketplace \
       --cov=rpc.methods.payments \
       tests/integration/test_marketplace_e2e.py

# Run specific test
pytest tests/integration/test_marketplace_e2e.py::TestEndToEndFlow::test_full_purchase_flow -v
```

### Payment Gateway Testing

#### Stripe Test Cards

```
Success:        4242 4242 4242 4242
Decline:        4000 0000 0000 0002
3D Secure:      4000 0025 0000 3155
```

#### PayPal Sandbox

1. Create sandbox account at `https://sandbox.paypal.com`
2. Test payments in sandbox mode
3. Verify webhooks in sandbox account

#### Manual Testing Workflow

```bash
# 1. Start RPC server
python -m rpc.server

# 2. Start Explorer
cd explorer-web && npm run dev

# 3. Start Wallet
cd wallet && flutter run

# 4. Test purchase flow:
#    - Open wallet, go to /marketplace/buy
#    - Enter 100 ANM
#    - Select payment method
#    - Complete payment with test card
#    - Verify webhook received
#    - Check purchase history

# 5. Verify treasury state
curl -X POST http://127.0.0.1:8545 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "explorer_getTreasurySnapshot",
    "params": [],
    "id": 1
  }'
```

## RPC Method Reference

### Treasury & Pricing

**explorer_getTreasurySnapshot()**
```json
{
  "jsonrpc": "2.0",
  "method": "explorer_getTreasurySnapshot",
  "params": [],
  "id": 1
}
```

Response:
```json
{
  "totalSupply": 1000000000000000000000000000,
  "soldToDate": 345000000000000000000000000,
  "treasuryBalance": 655000000000000000000000000,
  "percentSold": 34.5,
  "revenueToDate": 450000000000000000000000000,
  "targetRevenue": 1000000000000000000000000000,
  "yearsToTarget": 9.2
}
```

**explorer_getMarketData(token)**
```json
{
  "jsonrpc": "2.0",
  "method": "explorer_getMarketData",
  "params": {"token": "ANM"},
  "id": 1
}
```

Response:
```json
{
  "price": 1.50,
  "marketCap": 1500000000,
  "volume24h": 45000000,
  "change24h": 12.5,
  "change7d": 35.2,
  "high24h": 1.55,
  "low24h": 1.40,
  "lastUpdate": "2024-11-24T12:00:00Z",
  "source": "coingecko"
}
```

**explorer_getPriceHistory(token, days)**
```json
{
  "jsonrpc": "2.0",
  "method": "explorer_getPriceHistory",
  "params": {"token": "ANM", "days": 7},
  "id": 1
}
```

Response:
```json
{
  "prices": [1.0, 1.07, 1.14, 1.21, 1.28, 1.35, 1.42],
  "timestamps": ["2024-11-17T12:00:00Z", ...],
  "period": "7d",
  "currency": "USD"
}
```

**marketplace_getPricingCurve()**
```json
{
  "jsonrpc": "2.0",
  "method": "marketplace_getPricingCurve",
  "params": [],
  "id": 1
}
```

Response:
```json
{
  "basePrice": 1.0,
  "markupPercentage": 0.15,
  "treasuryMultiplierFormula": "1.0 + 2.0 * sqrt(percentSold)",
  "treasuryTargetRevenue": 1000000000000000000000000000,
  "deterministic": true,
  "formula": "max($1.00, exchangePrice * 1.15) * treasuryMultiplier"
}
```

**marketplace_calculatePrice(marketPrice, percentSold)**
```json
{
  "jsonrpc": "2.0",
  "method": "marketplace_calculatePrice",
  "params": {
    "marketPrice": 1.50,
    "percentSold": 34.5,
    "basePrice": 1.0,
    "markupPercentage": 0.15
  },
  "id": 1
}
```

Response:
```json
{
  "exchangePrice": 1.725,
  "effectivePrice": 2.595,
  "treasuryMultiplier": 1.5,
  "basePrice": 1.0,
  "markupPercentage": 0.15,
  "percentSold": 34.5
}
```

### Purchase History

**wallet_getPurchaseHistory(address, limit, offset)**
```json
{
  "jsonrpc": "2.0",
  "method": "wallet_getPurchaseHistory",
  "params": {
    "address": "0x1234567890123456789012345678901234567890",
    "limit": 50,
    "offset": 0
  },
  "id": 1
}
```

Response:
```json
{
  "purchases": [
    {
      "id": "purchase_1",
      "timestamp": "2024-11-24T10:00:00Z",
      "anmQuantity": 1000,
      "usdAmount": 1500,
      "pricePerAnm": 1.50,
      "paymentMethod": "stripe",
      "status": "completed",
      "receiptUrl": "https://receipts.stripe.com/...",
      "transactionHash": "0xabc123..."
    }
  ],
  "totalPurchases": 1,
  "totalAnmPurchased": 1000,
  "totalSpent": 1500,
  "averagePrice": 1.50
}
```

## Deep Linking

### Supported Deep Links

From Explorer to Wallet:
- `animica://marketplace/buy` — Open wallet purchase page
- `animica://marketplace/history` — Open purchase history
- `animica://marketplace/treasury` — Open treasury dashboard

From Wallet to Explorer:
- `https://explorer.animica.io/marketplace` — Open explorer marketplace
- `https://explorer.animica.io/tx/<hash>` — Open transaction details
- `https://explorer.animica.io/address/<address>` — Open address view

### Usage in Code

```typescript
// React Explorer
function openWallet() {
  window.open('animica://marketplace/buy');
}

// Flutter Wallet
void openExplorer() {
  launchUrl(Uri.parse('https://explorer.animica.io/marketplace'));
}
```

## Production Deployment

### Environment Setup

For production, update `.env` with:
- `STRIPE_SECRET_KEY=sk_live_...`
- `PAYPAL_SANDBOX=false`
- `DEBUG=false`
- `LOG_LEVEL=ERROR`
- Database credentials (PostgreSQL recommended)

### SSL/HTTPS

All payment webhooks must use HTTPS.

```bash
# Using Let's Encrypt with nginx
certbot certonly --standalone -d your-domain.com
```

### Database Migration

```bash
# Backup
pg_dump animica_marketplace > backup.sql

# Migrate schema
python -m alembic upgrade head

# Verify
psql -d animica_marketplace -c "SELECT * FROM purchases LIMIT 1;"
```

### Monitoring

Set up monitoring for:
- RPC endpoint health
- Payment webhook latency
- Treasury state consistency
- Error rates in payment processing

## Troubleshooting

### RPC Connection Issues

```bash
# Test RPC connectivity
curl -X POST http://127.0.0.1:8545 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "net_version", "id": 1}'

# Check logs
tail -f logs/rpc.log | grep marketplace
```

### Payment Webhook Not Received

1. Verify webhook endpoint is accessible
2. Check webhook signing secret matches
3. Review payment processor webhook logs
4. Test with webhook CLI:
   ```bash
   stripe listen --forward-to localhost:8000/webhooks/payment/stripe
   ```

### Treasury State Mismatch

1. Verify all purchases recorded in database
2. Check contract state on blockchain
3. Replay webhooks if needed
4. Reconcile revenue calculations

## Support

For issues or questions:
1. Check `SECURITY.md` for responsible disclosure
2. Review architecture docs in `spec/`
3. Check existing issues/PRs
4. Post in community channels

## License

Apache 2.0 - See LICENSE.txt
