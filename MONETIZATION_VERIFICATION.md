# Monetization Implementation Verification Checklist

This document provides steps to verify the monetization hooks and billing infrastructure.

## Quick Verification

Run the billing and AICF tests:

```bash
# Run all billing tests
python3 -m pytest billing/tests/ -v

# Run AICF accountant tests
python3 -m pytest aicf/tests/test_accountant.py -v

# Run DA fee tests (may skip in lightweight env)
python3 -m pytest da/tests/test_fees.py -v

# Expected: 40+ tests passed
```

## Environment Variable Testing

### Test 1: Default Free Mode

```bash
# Clear all monetization env vars
unset ANIMICA_BILLING_MODE
unset ANIMICA_API_KEY_HEADER
unset ANIMICA_DA_FEE_PER_BYTE
unset ANIMICA_RPC_FEE_FLAT
unset ANIMICA_AICF_BILLING_MODE

# Run billing config test
python3 -c "
from billing.config import load_billing_config
config = load_billing_config()
assert config.mode == 'free'
assert config.da_fee.fee_per_byte == 0.0
assert config.rpc_fee.fee_flat == 0.0
assert config.aicf.mode == 'free'
print('✅ Free mode defaults correct')
"
```

### Test 2: Paid Mode Configuration

```bash
# Set paid mode env vars
export ANIMICA_BILLING_MODE=paid
export ANIMICA_DA_FEE_PER_BYTE=0.001
export ANIMICA_RPC_FEE_FLAT=0.01
export ANIMICA_FEE_TREASURY_ADDRESS=0x1234567890123456789012345678901234567890
export ANIMICA_FEE_VALIDATOR_SPLIT=0.8
export ANIMICA_AICF_BILLING_MODE=paid
export ANIMICA_AICF_RATE_PER_UNIT=0.5
export ANIMICA_AICF_FREE_UNITS=500

# Run billing config test
python3 -c "
from billing.config import load_billing_config
config = load_billing_config()
assert config.mode == 'paid'
assert config.da_fee.fee_per_byte == 0.001
assert config.rpc_fee.fee_flat == 0.01
assert config.da_fee.validator_split == 0.8
assert config.aicf.mode == 'paid'
assert config.aicf.rate_per_unit == 0.5
print('✅ Paid mode configuration correct')
"

# Clean up
unset ANIMICA_BILLING_MODE
unset ANIMICA_DA_FEE_PER_BYTE
unset ANIMICA_RPC_FEE_FLAT
unset ANIMICA_FEE_TREASURY_ADDRESS
unset ANIMICA_FEE_VALIDATOR_SPLIT
unset ANIMICA_AICF_BILLING_MODE
unset ANIMICA_AICF_RATE_PER_UNIT
unset ANIMICA_AICF_FREE_UNITS
```

## Functional Testing

### Test 3: Usage Store

```bash
python3 << 'PYTHON'
from billing.store import UsageStore

store = UsageStore()

# Test request tracking
store.increment_requests("key1", count=10)
record = store.get_record("key1")
assert record.requests_count == 10
print("✅ Usage store request tracking works")

# Test rate limiting
allowed, count = store.check_rate_limit("key2", limit_rpm=5)
assert allowed and count == 1
for _ in range(4):
    allowed, count = store.check_rate_limit("key2", limit_rpm=5)
    assert allowed
allowed, count = store.check_rate_limit("key2", limit_rpm=5)
assert not allowed
print("✅ Rate limiting works")
PYTHON
```

### Test 4: DA Fee Calculation

```bash
python3 << 'PYTHON'
from da.fees import calculate_da_fee

# Test free mode (default)
receipt = calculate_da_fee(1024)
assert receipt.total_fee == 0.0
print("✅ DA fees in free mode: 0")

# Test fee calculation
import os
os.environ["ANIMICA_DA_FEE_PER_BYTE"] = "0.001"
os.environ["ANIMICA_FEE_VALIDATOR_SPLIT"] = "0.8"
receipt = calculate_da_fee(1024)
assert abs(receipt.total_fee - 1.024) < 0.0001
assert abs(receipt.validator_amount - 0.8192) < 0.0001
assert abs(receipt.treasury_amount - 0.2048) < 0.0001
print("✅ DA fee calculation and split correct")

# Clean up
del os.environ["ANIMICA_DA_FEE_PER_BYTE"]
del os.environ["ANIMICA_FEE_VALIDATOR_SPLIT"]
PYTHON
```

### Test 5: AICF Job Accounting

```bash
python3 << 'PYTHON'
from aicf.accountant import JobAccountant

# Test free mode with quota
accountant = JobAccountant(
    rate_per_unit=0.0,
    free_units=1000,
    mode="free",
)

# Within quota
record = accountant.record_job("job1", "user1", "free", 500)
assert record.charge == 0.0
print("✅ AICF free mode within quota")

# Exceeds quota
try:
    record = accountant.record_job("job2", "user1", "free", 600)
    assert False, "Should raise ValueError"
except ValueError as e:
    assert "exceed free tier quota" in str(e)
    print("✅ AICF free mode quota enforcement")

# Test paid mode
accountant = JobAccountant(
    rate_per_unit=0.5,
    free_units=1000,
    mode="paid",
)

record = accountant.record_job("job1", "user1", "pro", 500)
assert record.charge == 0.0  # Within free tier

record = accountant.record_job("job2", "user1", "pro", 600)
assert abs(record.charge - 50.0) < 0.01  # 100 billable * 0.5
print("✅ AICF paid mode billing works")
PYTHON
```

### Test 6: File-Backed Persistence

```bash
python3 << 'PYTHON'
import tempfile
from pathlib import Path
from billing.store import FileBackedUsageStore

with tempfile.TemporaryDirectory() as tmpdir:
    file_path = Path(tmpdir) / "usage.json"
    
    # Create store and add data
    store1 = FileBackedUsageStore(file_path, auto_save_interval=0.1)
    store1.increment_requests("key1", count=100)
    store1.save()
    
    # Load in new store
    store2 = FileBackedUsageStore(file_path)
    record = store2.get_record("key1")
    assert record.requests_count == 100
    print("✅ File-backed persistence works")
PYTHON
```

## Integration Testing

### Test 7: API Key Middleware (Optional)

If RPC server is running:

```bash
# Test without API key (should work in free mode)
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getChainId","params":[],"id":1}' \
  | jq .

# Enable paid mode and restart server
export ANIMICA_BILLING_MODE=paid

# Test without API key (should return 401)
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getChainId","params":[],"id":1}' \
  | jq .

# Test with invalid API key (should return 401)
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -H "x-animica-key: invalid_key" \
  -d '{"jsonrpc":"2.0","method":"chain.getChainId","params":[],"id":1}' \
  | jq .

# Test with valid API key (should succeed)
# First create valid_keys.json with test keys
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -H "x-animica-key: test_key_free" \
  -d '{"jsonrpc":"2.0","method":"chain.getChainId","params":[],"id":1}' \
  | jq .
```

## Full Test Suite

Run the complete test suite:

```bash
# Run all Python tests
./testall.sh

# Or run just Python tests
pytest

# Expected: All tests pass (may have some skipped in lightweight env)
```

## Extension Points for Production

The monetization system provides clean extension points for production deployment:

### 1. Stripe Integration

Add to `rpc/methods/payments.py`:
- Verify webhook signatures using Stripe secret
- Process `payment_intent.succeeded` events
- Mint tokens via treasury contract

### 2. Crypto Payments

Monitor on-chain payments to treasury address:
- Set up event listener for treasury contract
- Verify payment amount and sender
- Credit user account or update plan
- Emit receipt event

### 3. Database Backend

Replace file-backed stores with database:
- Usage tracking: PostgreSQL or Redis
- API keys: Database with hashing
- Job accounting: Time-series database for analytics

### 4. Quota Reset

Implement periodic quota resets:
- Schedule cron job for monthly/daily resets
- Call `accountant.reset_submitter_usage(submitter)`
- Send notifications to users near limits

### 5. Monitoring

Add monitoring and alerting:
- Track revenue metrics (Prometheus/Grafana)
- Alert on payment failures
- Monitor rate limit hits
- Track per-plan usage patterns

## Known Limitations

1. **In-Memory Default**: Usage tracking defaults to in-memory, lost on restart. Use file-backed or database for production.

2. **Simple Rate Limiting**: Token bucket algorithm is simple. Consider distributed rate limiting (Redis) for multi-instance deployments.

3. **Mock Payment Processing**: Payment webhook handlers are minimal. Implement full signature verification and error handling for production.

4. **No User Management**: No built-in user accounts or plan management UI. Add admin panel or integrate with existing auth system.

5. **Fixed Treasury Split**: Split percentage is global. May want per-user or dynamic splits.

## Verification Summary

After running all tests:

- [ ] 40+ unit tests passed
- [ ] Default free mode confirmed
- [ ] Paid mode configuration works
- [ ] Usage tracking verified
- [ ] Rate limiting enforced
- [ ] DA fee calculation correct
- [ ] AICF billing works
- [ ] File persistence functional
- [ ] Documentation complete

## Next Steps

1. Deploy to staging with paid mode enabled
2. Create API keys for test users
3. Monitor usage metrics
4. Integrate payment processor webhooks
5. Add admin dashboard for key/plan management
6. Set production fee rates
7. Deploy to mainnet

## Support

For issues or questions:
- Check docs/monetization.md for detailed documentation
- Review test files for usage examples
- Open GitHub issue for bugs or feature requests
