# Animica Monetization Guide

This document describes the monetization hooks and billing infrastructure in Animica, including how to configure and use them for hosted infrastructure, API access, and resource billing.

## Overview

Animica provides a flexible, environment-driven monetization system that operates in two modes:

- **Free/Devnet Mode (default)**: All services are free with rate limits but no authentication required. Fees are zero.
- **Paid Mode**: API key authentication required, fees collected for DA posting and RPC calls, AICF jobs billed based on resource consumption.

## Architecture

The monetization system consists of several integrated components:

### 1. Billing Configuration Module (`billing/`)

Central configuration for all monetization settings:

- **Plans**: Free, Pro, Enterprise with different rate limits and fees
- **DA Fees**: Per-byte fees for Data Availability posting
- **RPC Fees**: Flat fees per RPC call
- **AICF Billing**: Per-unit fees for AI/Quantum compute jobs
- **Treasury Split**: Configurable split between validators and treasury

### 2. API Key Middleware

Middleware for RPC and studio-services that:

- Validates API keys
- Looks up user plans (free/pro/enterprise)
- Enforces per-plan rate limits (requests per minute)
- Tracks usage metrics
- Returns clean 401/429 errors (no stack traces)

### 3. Usage Tracking

In-memory and file-backed stores for tracking:

- Request counts per API key
- DA bytes posted
- RPC calls made
- AICF resource units consumed
- Rate limit windows

### 4. DA Fee Accounting

Calculates and tracks fees for Data Availability operations:

- Fee = bytes × ANIMICA_DA_FEE_PER_BYTE
- Splits fees between validators and treasury
- Includes fee receipts in responses

### 5. AICF Job Accounting

Tracks costs for AI/Quantum compute jobs:

- Free tier with quota (default 1000 units)
- Per-unit pricing after free tier exhausted
- Quota enforcement in free mode
- Pay-as-you-go in paid mode

## Environment Variables

All monetization settings are configured via environment variables. Defaults support free/devnet usage.

### General Billing

```bash
# Billing mode: "free" (default) or "paid"
ANIMICA_BILLING_MODE=free

# API key header name
ANIMICA_API_KEY_HEADER=x-animica-key

# Default plan for new/unknown users
ANIMICA_DEFAULT_PLAN=free
```

### Rate Limits (requests per minute)

```bash
# Free tier rate limit
ANIMICA_RATE_LIMIT_FREE=60

# Pro tier rate limit
ANIMICA_RATE_LIMIT_PRO=600

# Enterprise tier rate limit (optional, default 6000)
ANIMICA_RATE_LIMIT_ENTERPRISE=6000
```

### DA Fees

```bash
# Fee per byte for DA posting (default 0 in devnet)
ANIMICA_DA_FEE_PER_BYTE=0.0

# Treasury address for fee collection
ANIMICA_FEE_TREASURY_ADDRESS=""

# Fraction of fees to validators (0-1, default 1.0 = all to validators)
ANIMICA_FEE_VALIDATOR_SPLIT=1.0
```

### RPC Fees

```bash
# Flat fee per RPC call (default 0)
ANIMICA_RPC_FEE_FLAT=0.0
```

### AICF Billing

```bash
# AICF billing mode: "free" (default) or "paid"
ANIMICA_AICF_BILLING_MODE=free

# Cost per AICF resource unit (default 0)
ANIMICA_AICF_RATE_PER_UNIT=0.0

# Free tier units per period (default 1000)
ANIMICA_AICF_FREE_UNITS=1000
```

## Usage Examples

### Free/Devnet Mode (Default)

No configuration needed. All services are free:

```bash
# Start RPC server (free mode by default)
python -m rpc.server
```

API calls work without authentication:

```bash
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'
```

### Paid Mode with API Keys

Enable paid mode and configure fees:

```bash
export ANIMICA_BILLING_MODE=paid
export ANIMICA_API_KEY_HEADER=x-api-key
export ANIMICA_RATE_LIMIT_FREE=60
export ANIMICA_RATE_LIMIT_PRO=600
export ANIMICA_DA_FEE_PER_BYTE=0.001
export ANIMICA_RPC_FEE_FLAT=0.01
export ANIMICA_FEE_TREASURY_ADDRESS=0x1234567890123456789012345678901234567890
export ANIMICA_FEE_VALIDATOR_SPLIT=0.8
export ANIMICA_AICF_BILLING_MODE=paid
export ANIMICA_AICF_RATE_PER_UNIT=0.5
export ANIMICA_AICF_FREE_UNITS=500

# Start RPC server
python -m rpc.server
```

API calls require authentication:

```bash
# Without API key - returns 401 Unauthorized
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'

# With valid API key - succeeds
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-api-key-here" \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'
```

### DA Fee Example

When posting data to DA in paid mode:

```bash
# Post 1024 bytes with fee_per_byte = 0.001
# Total fee = 1024 * 0.001 = 1.024 tokens
# Validator amount (80%) = 0.8192
# Treasury amount (20%) = 0.2048

curl -X POST http://localhost:8082/da/put \
  -H "x-api-key: your-api-key" \
  -F "namespace=100" \
  -F "data=@myfile.bin"
```

Response includes fee breakdown:

```json
{
  "commitment": "0xabc123...",
  "fee": {
    "bytes_posted": 1024,
    "total_fee": 1.024,
    "validator_amount": 0.8192,
    "treasury_amount": 0.2048,
    "fee_per_byte": 0.001,
    "treasury_address": "0x1234...",
    "validator_split": 0.8
  }
}
```

### AICF Job Billing Example

In free mode with quota:

```python
from aicf.accountant import JobAccountant

# Create accountant in free mode with 1000 unit quota
accountant = JobAccountant(
    rate_per_unit=0.0,
    free_units=1000,
    mode="free",
)

# Record jobs within quota
record1 = accountant.record_job(
    job_id="job1",
    submitter="user1",
    plan="free",
    resource_units=500,
)
print(f"Charge: {record1.charge}")  # 0.0 (within free tier)

# Attempt to exceed quota raises error
try:
    record2 = accountant.record_job(
        job_id="job2",
        submitter="user1",
        plan="free",
        resource_units=600,  # Would total 1100, exceeds 1000
    )
except ValueError as e:
    print(e)  # "Job would exceed free tier quota..."
```

In paid mode:

```python
# Create accountant in paid mode
accountant = JobAccountant(
    rate_per_unit=0.5,
    free_units=1000,
    mode="paid",
)

# First job within free tier
record1 = accountant.record_job(
    job_id="job1",
    submitter="user1",
    plan="pro",
    resource_units=800,
)
print(f"Charge: {record1.charge}")  # 0.0

# Second job partially billable
record2 = accountant.record_job(
    job_id="job2",
    submitter="user1",
    plan="pro",
    resource_units=300,
)
print(f"Charge: {record2.charge}")  # 50.0 (100 billable units * 0.5)
```

## API Key Management

### Creating API Keys

Generate API keys for users:

```python
import secrets

def generate_api_key():
    """Generate a secure API key."""
    return "ak_" + secrets.token_urlsafe(32)

api_key = generate_api_key()
print(f"API Key: {api_key}")
```

### Managing Valid Keys

Store valid keys in a JSON file or database:

```json
{
  "ak_free_user_123": "free",
  "ak_pro_user_456": "pro",
  "ak_enterprise_789": "enterprise"
}
```

Load keys when starting the server:

```python
from rpc.middleware.api_key import build_api_key_middleware

middleware = build_api_key_middleware(
    app,
    usage_store_path="./data/usage.json",
    valid_keys_path="./data/valid_keys.json",
)
```

## Rate Limit Handling

When rate limits are exceeded, the API returns a 429 error:

```json
{
  "error": {
    "code": -32005,
    "message": "Rate limit exceeded",
    "data": {
      "limit_rpm": 60,
      "current_requests": 60,
      "retry_after_seconds": 60,
      "hint": "Upgrade your plan for higher rate limits"
    }
  }
}
```

Client should respect the `Retry-After` header and implement exponential backoff.

## Usage Tracking and Persistence

### In-Memory Store

Default usage tracking (lost on restart):

```python
from billing.store import UsageStore

store = UsageStore()
store.increment_requests("api_key_123", count=1)
store.increment_da_bytes("api_key_123", 1024)
```

### File-Backed Store

Persistent usage tracking:

```python
from billing.store import FileBackedUsageStore

store = FileBackedUsageStore(
    file_path="./data/usage.json",
    auto_save_interval=60.0,  # Auto-save every 60 seconds
)

store.increment_requests("api_key_123", count=1)
# Automatically saved after 60 seconds
```

## Integration Points for Payment Processing

The current implementation provides hooks for integration with payment processors (Stripe, PayPal, crypto):

### Stripe Integration

Add webhook handlers in `rpc/methods/payments.py`:

```python
@method("marketplace_processStripeWebhook")
async def process_stripe_webhook(ctx, body, signature):
    # Verify webhook signature
    # Extract payment details
    # Mint tokens on-chain
    # Return confirmation
    pass
```

### Crypto Payment Integration

For native token payments:

1. User sends payment transaction to treasury address
2. Monitor chain for incoming payments
3. Verify payment amount and sender
4. Credit user account or mint tokens
5. Update user's plan if upgrading

### Custom Payment Gateway

Implement custom payment flow:

1. User initiates payment via marketplace RPC methods
2. Payment gateway processes transaction
3. Gateway sends confirmation to webhook endpoint
4. System verifies payment and credits account
5. Return success/failure to user

## Monitoring and Metrics

Track monetization metrics:

```python
from billing.store import UsageStore

store = UsageStore()

# Get usage for an API key
record = store.get_record("api_key_123")
print(f"Requests: {record.requests_count}")
print(f"DA bytes: {record.da_bytes_posted}")
print(f"RPC calls: {record.rpc_calls}")
print(f"AICF units: {record.aicf_units_used}")

# Get all usage records
all_records = store.get_all_records()
total_requests = sum(r.requests_count for r in all_records.values())
```

Export metrics to Prometheus (if enabled):

```bash
# Metrics endpoint
curl http://localhost:9100/metrics
```

## Security Considerations

1. **API Key Storage**: Store API keys securely (hashed in production)
2. **Rate Limiting**: Prevents abuse and DDoS attacks
3. **Treasury Address**: Use multi-sig wallet for production
4. **Webhook Signatures**: Always verify webhook signatures
5. **HTTPS**: Use TLS for all API endpoints in production
6. **Audit Logs**: Log all payment and usage events

## Migration Path

To migrate from free to paid mode:

1. **Phase 1**: Deploy with ANIMICA_BILLING_MODE=free
   - All existing users continue working
   - No authentication required
   - Fees remain at zero

2. **Phase 2**: Enable authentication but keep fees zero
   - Set ANIMICA_BILLING_MODE=paid
   - Issue API keys to existing users
   - Communicate migration timeline

3. **Phase 3**: Enable fees gradually
   - Start with low fees
   - Monitor usage and revenue
   - Adjust fees based on metrics

4. **Phase 4**: Full paid mode
   - Production fee rates
   - Multiple plan tiers
   - Payment processing integrated

## Troubleshooting

### Authentication Issues

**Problem**: "API key required in paid mode"

**Solution**: Ensure you're passing the API key in the correct header (default `x-animica-key`)

```bash
curl -H "x-animica-key: your-key-here" ...
```

### Rate Limit Issues

**Problem**: "Rate limit exceeded"

**Solution**:
1. Wait for the retry_after_seconds period
2. Implement exponential backoff in client
3. Upgrade to higher plan tier
4. Request rate limit increase

### Fee Calculation Issues

**Problem**: Fee amounts don't match expected values

**Solution**:
1. Check environment variables are set correctly
2. Verify fee_per_byte and validator_split values
3. Review usage records in store
4. Check for floating-point precision issues

## Support and Resources

- **Source Code**: `billing/`, `rpc/middleware/api_key.py`, `aicf/accountant.py`, `da/fees.py`
- **Tests**: `billing/tests/`, `aicf/tests/test_accountant.py`, `da/tests/test_fees.py`
- **Examples**: See usage examples in this document

For questions or issues, please open a GitHub issue or contact the development team.
