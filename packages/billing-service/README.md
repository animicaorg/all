# Animica Compute Platform - Billing Service

FastAPI-based billing and payments service with support for Stripe, PayPal, and ANM token payments.

## Features

- **Credit Ledger**: Track credits, debits, and balances per user/org
- **Stripe Integration**: Subscriptions, one-time payments, webhooks
- **PayPal Integration**: Alternative payment method
- **ANM Token Payments**: Native Animica blockchain payments
- **Subscription Plans**: Starter, Pro, Enterprise tiers
- **Usage Tracking**: Token usage, GPU seconds, code execution
- **Rate Limiting**: Per-user and per-org limits
- **Invoicing**: Generate and store invoices/receipts
- **Webhook Handling**: Idempotent webhook processing
- **Refunds**: Handle refunds and disputes

## API Endpoints

### Balance & Credits
- `GET /balance` - Get current credit balance
- `GET /balance/history` - Get balance history
- `POST /credits/purchase` - Purchase credits

### Subscriptions
- `GET /subscriptions` - List subscriptions
- `POST /subscriptions` - Create subscription
- `PATCH /subscriptions/{sub_id}` - Update subscription
- `DELETE /subscriptions/{sub_id}` - Cancel subscription

### Payment Methods
- `GET /payment-methods` - List payment methods
- `POST /payment-methods` - Add payment method
- `DELETE /payment-methods/{pm_id}` - Remove payment method

### Invoices
- `GET /invoices` - List invoices
- `GET /invoices/{invoice_id}` - Get invoice details
- `GET /invoices/{invoice_id}/pdf` - Download invoice PDF

### Usage
- `GET /usage` - Get usage statistics
- `GET /usage/detailed` - Detailed usage breakdown
- `POST /usage/record` - Record usage (internal)

### ANM Payments
- `POST /anm/payment-intent` - Create ANM payment intent
- `GET /anm/payment-intent/{intent_id}` - Get payment intent status
- `POST /anm/payment-intent/{intent_id}/confirm` - Confirm on-chain payment

### Webhooks
- `POST /webhooks/stripe` - Stripe webhook endpoint
- `POST /webhooks/paypal` - PayPal webhook endpoint

## Database Schema

### Credit Ledger
```sql
CREATE TABLE credit_ledger (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    organization_id UUID REFERENCES organizations(id),
    amount DECIMAL(20, 8) NOT NULL,
    balance_after DECIMAL(20, 8) NOT NULL,
    type VARCHAR(50) NOT NULL, -- 'credit', 'debit', 'refund'
    reason VARCHAR(255),
    reference_id VARCHAR(255),
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Subscriptions
```sql
CREATE TABLE subscriptions (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    organization_id UUID REFERENCES organizations(id),
    plan VARCHAR(50) NOT NULL, -- 'starter', 'pro', 'enterprise'
    status VARCHAR(50) NOT NULL, -- 'active', 'cancelled', 'expired'
    stripe_subscription_id VARCHAR(255),
    current_period_start TIMESTAMP,
    current_period_end TIMESTAMP,
    cancel_at_period_end BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### Payment Methods
```sql
CREATE TABLE payment_methods (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    type VARCHAR(50) NOT NULL, -- 'card', 'paypal', 'wallet'
    stripe_payment_method_id VARCHAR(255),
    paypal_billing_agreement_id VARCHAR(255),
    wallet_address VARCHAR(255),
    is_default BOOLEAN DEFAULT FALSE,
    last_four VARCHAR(4),
    brand VARCHAR(50),
    exp_month INT,
    exp_year INT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Invoices
```sql
CREATE TABLE invoices (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    organization_id UUID REFERENCES organizations(id),
    amount DECIMAL(20, 2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'USD',
    status VARCHAR(50) NOT NULL, -- 'draft', 'paid', 'void', 'refunded'
    stripe_invoice_id VARCHAR(255),
    description TEXT,
    pdf_url TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    paid_at TIMESTAMP
);
```

### Usage Records
```sql
CREATE TABLE usage_records (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    organization_id UUID REFERENCES organizations(id),
    resource_type VARCHAR(50) NOT NULL, -- 'tokens', 'gpu_seconds', 'code_execution'
    quantity DECIMAL(20, 8) NOT NULL,
    cost_credits DECIMAL(20, 8) NOT NULL,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### ANM Payment Intents
```sql
CREATE TABLE anm_payment_intents (
    id UUID PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    amount_anm DECIMAL(20, 8) NOT NULL,
    amount_credits DECIMAL(20, 8) NOT NULL,
    wallet_address VARCHAR(255) NOT NULL,
    tx_hash VARCHAR(255),
    status VARCHAR(50) NOT NULL, -- 'pending', 'confirmed', 'failed', 'expired'
    confirmations INT DEFAULT 0,
    required_confirmations INT DEFAULT 6,
    created_at TIMESTAMP DEFAULT NOW(),
    confirmed_at TIMESTAMP,
    expires_at TIMESTAMP
);
```

## Subscription Plans

### Starter (Free)
- 1,000 credits/month
- Rate limit: 100 req/min
- Community support

### Pro ($29/month)
- 50,000 credits/month
- Rate limit: 1000 req/min
- Priority support
- Advanced features

### Enterprise (Custom)
- Custom credits
- Custom rate limits
- Dedicated support
- SLA guarantees
- On-premise deployment

## Credit Pricing

- **LLM Inference**: 10 credits per 1k tokens (varies by model)
- **Code Execution**: 1 credit per second
- **GPU Time**: 100 credits per minute

## Development

```bash
cd packages/billing-service
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn billing_service.main:app --reload --port 8002
```

## Environment Variables

See `.env.compute.example` for configuration.

## Stripe Webhooks

Configure webhook endpoint: `https://your-domain.com/v1/billing/webhooks/stripe`

Required events:
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.payment_succeeded`
- `invoice.payment_failed`
- `charge.refunded`

## Security

- Webhook signature verification
- Idempotent payment processing
- Rate limiting on payment endpoints
- Encryption for sensitive data
- PCI DSS compliance considerations
