# AICF Operations Runbook

## Overview

This runbook provides operational guidance for monitoring and maintaining the AICF (AI Compute Fund) system.

## Metrics Monitoring

### Endpoint

- **Prometheus metrics**: `http://<node>:8545/metrics`
- **Health check**: `http://<node>:8545/healthz`

### Key Metrics

#### Pool Metrics

- `animica_aicf_pool_balance_total` - Current AICF pool balance (ANM)
- `animica_aicf_inflows_total{source}` - Total inflows by source (block_reward, fees, ena, governance)

#### Provider Metrics

- `animica_aicf_provider_accrued_total{provider_id}` - Provider accrued rewards (ANM)
- `animica_aicf_claims_total{provider_id,status}` - Claim attempts (success/failed)

#### ENA Metrics

- `animica_aicf_ena_calls_total{provider_id,status}` - ENA inference calls
- `animica_aicf_ena_fee_total{split}` - Fee distribution (aicf/provider/treasury/burn)
- `animica_aicf_mempool_ena_pending` - Pending ENA transactions

#### Epoch Metrics

- `animica_aicf_epoch_height` - Current block height
- `animica_aicf_epoch_index` - Current epoch number

#### Error Metrics

- `animica_aicf_mempool_rejects_total{reason}` - Mempool rejections
- `animica_aicf_db_write_errors_total{operation}` - Database write failures
- `animica_aicf_read_only_fs_errors_total` - Read-only filesystem errors

## Alert Suggestions

### Critical Alerts

#### Pool Stuck

**Condition**: `rate(animica_aicf_inflows_total[1h]) == 0`

**Severity**: Critical

**Action**: Check:
1. Block production (`animica_aicf_epoch_height` increasing?)
2. Miner reward flow
3. ENA call activity
4. Governance top-up mechanisms

**Command**:
```bash
# Check recent pool inflows
curl http://localhost:8545/metrics | grep aicf_inflows_total

# Check block height progression
watch -n 5 'curl -s http://localhost:8545/metrics | grep epoch_height'
```

#### Claims Failing

**Condition**: `rate(animica_aicf_claims_total{status="failed"}[5m]) > 0.5`

**Severity**: Critical

**Action**: Check:
1. State DB writable (check health endpoint)
2. Provider accrued balances
3. Claim cooldown settings
4. Min claim amount configuration

**Command**:
```bash
# Check health status
curl http://localhost:8545/healthz | jq .

# Check claim errors in logs
journalctl -u animica-node --since "10 minutes ago" | grep "aicf.claim.error"
```

#### Database Read-Only

**Condition**: `animica_aicf_read_only_fs_errors_total > 0`

**Severity**: Critical

**Action**:
1. Check filesystem mount status: `df -h`
2. Check disk space: `df -i` (inodes)
3. Check mount options: `mount | grep <data-dir>`
4. Check filesystem errors: `dmesg | tail -100`

**Recovery**:
```bash
# Check mount
df -h /var/lib/animica

# Remount if needed (CAUTION: may require restart)
sudo mount -o remount,rw /var/lib/animica

# Restart node
sudo systemctl restart animica-node
```

### Warning Alerts

#### Provider Spam

**Condition**: `rate(animica_aicf_ena_calls_total{provider_id="X"}[5m]) > 100`

**Severity**: Warning

**Action**: Check:
1. Provider legitimacy
2. Fee payment (not spam if paying fees)
3. Worker attribution (is one worker spamming?)
4. Consider rate limiting if needed

**Command**:
```bash
# Check provider activity
curl http://localhost:8545/metrics | grep provider_id=\"X\"

# Check worker attribution
# TODO: Add RPC method for worker stats
```

#### High Mempool Rejections

**Condition**: `rate(animica_aicf_mempool_rejects_total[5m]) > 10`

**Severity**: Warning

**Action**: Check rejection reasons:
```bash
curl http://localhost:8545/metrics | grep mempool_rejects_total

# Common reasons:
# - low_fee: Fee below minimum
# - unknown_provider: Provider not registered
# - invalid_payload: Malformed transaction data
```

#### Database Write Errors

**Condition**: `animica_aicf_db_write_errors_total > 0`

**Severity**: Warning (escalates to Critical if persistent)

**Action**: Check:
1. Disk space: `df -h`
2. Filesystem health: `sudo fsck -n /dev/<device>`
3. I/O errors: `dmesg | grep -i error`

**Recovery**:
```bash
# Check disk space
df -h /var/lib/animica

# Check for I/O errors
dmesg | tail -50

# If disk full, clean up logs or snapshots
# If corruption detected, restore from backup
```

### Info Alerts

#### Low Pool Balance

**Condition**: `animica_aicf_pool_balance_total < 1000`

**Severity**: Info

**Action**: Consider governance top-up if:
1. Inflows are low
2. Claims are high
3. Pool is essential for ecosystem

**Command**:
```bash
# Check pool balance
curl http://localhost:8545/metrics | grep pool_balance_total

# Initiate governance top-up (requires authority)
animica aicf topup --amount 1000000000000 --memo "Emergency pool funding"
```

## Health Checks

### Endpoint

`GET http://localhost:8545/healthz`

### Response

```json
{
  "status": "healthy",
  "checks": {
    "state_db_writable": {
      "status": "ok",
      "message": "writable"
    },
    "mempool_available": {
      "status": "ok",
      "message": "available (size=42)"
    },
    "aicf_pool_balance": {
      "status": "ok",
      "message": "ok (balance=1000000000000)"
    }
  }
}
```

**Status Codes**:
- `200 OK`: All systems operational
- `503 Service Unavailable`: One or more checks failed

### Kubernetes/Docker Health Probes

```yaml
livenessProbe:
  httpGet:
    path: /healthz
    port: 8545
  initialDelaySeconds: 30
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /healthz
    port: 8545
  initialDelaySeconds: 10
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 2
```

## Common Issues

### Issue: Claims Timing Out

**Symptoms**: `aicf.claim` transactions stuck in mempool

**Diagnosis**:
```bash
# Check mempool
animica mempool list | grep aicf_claim

# Check nonce sequence
animica account nonce <address>
```

**Resolution**:
1. Check nonce gaps
2. Ensure sufficient gas/fee
3. Verify claim cooldown satisfied

### Issue: ENA Calls Rejected

**Symptoms**: `ena_call` transactions rejected with `low_fee`

**Diagnosis**:
```bash
# Check fee parameters
curl http://localhost:8545/rpc -X POST -H "Content-Type: application/json" -d '{
  "jsonrpc": "2.0",
  "method": "ena_getFeeParams",
  "id": 1
}'
```

**Resolution**:
1. Increase fee in transaction
2. Check min_fee_nano configuration
3. Verify provider_id is valid

### Issue: Worker Attribution Missing

**Symptoms**: Worker stats not updating

**Diagnosis**:
```bash
# Check worker registration
# TODO: Add RPC method
curl http://localhost:8545/rpc -X POST -H "Content-Type: application/json" -d '{
  "jsonrpc": "2.0",
  "method": "aicf_getWorkerInfo",
  "params": ["provider_id", "worker_id"],
  "id": 1
}'
```

**Resolution**:
1. Ensure worker is registered
2. Check receipts include worker_id
3. Verify worker signature (if enabled)

## Prometheus Scrape Configuration

```yaml
scrape_configs:
  - job_name: 'animica-aicf'
    static_configs:
      - targets: ['localhost:8545']
    metrics_path: '/metrics'
    scrape_interval: 15s
    scrape_timeout: 10s
```

## Grafana Dashboard Queries

### Pool Balance Over Time

```promql
animica_aicf_pool_balance_total
```

### Inflow Rate by Source

```promql
rate(animica_aicf_inflows_total[5m])
```

### Provider Accrued Rewards (Top 10)

```promql
topk(10, animica_aicf_provider_accrued_total)
```

### Claim Success Rate

```promql
rate(animica_aicf_claims_total{status="success"}[5m]) 
/ 
rate(animica_aicf_claims_total[5m])
```

### ENA Call Latency (if instrumented)

```promql
histogram_quantile(0.99, rate(animica_aicf_ena_call_duration_seconds_bucket[5m]))
```

## Maintenance

### Routine Checks (Daily)

1. Pool balance trending
2. Claim success rates
3. ENA call activity
4. Mempool health
5. Database size

### Weekly Checks

1. Provider reward distribution fairness
2. Worker attribution accuracy
3. Governance top-up history
4. Alert rule effectiveness

### Monthly Checks

1. Full metric review
2. Capacity planning (disk, network)
3. Update ops runbook based on incidents
4. Review and tune alert thresholds

## Contact

For operational issues:
- **Discord**: #aicf-ops
- **Email**: ops@animica.org
- **Escalation**: Page on-call via PagerDuty

---

**Last Updated**: 2026-02-19
**Version**: 1.0.0
**Maintainer**: AICF Operations Team
