# CEX Monitoring Guide

## Table of Contents
1. [Overview](#overview)
2. [Monitoring Stack](#monitoring-stack)
3. [Metrics](#metrics)
4. [Alerts](#alerts)
5. [Dashboards](#dashboards)
6. [Log Aggregation](#log-aggregation)
7. [Troubleshooting](#troubleshooting)

---

## Overview

This guide covers the monitoring and observability setup for the CEX (Centralized Exchange) platform.

### Monitoring Goals

- **Visibility**: Real-time insight into system health and performance
- **Alerting**: Proactive notification of issues before they impact users
- **Debugging**: Tools to quickly diagnose and resolve problems
- **Capacity Planning**: Metrics to inform scaling decisions
- **Compliance**: Audit trails and transaction logs

### Key Metrics

- **Golden Signals** (Latency, Traffic, Errors, Saturation)
- **Business Metrics** (Orders, Trades, Volume, Users)
- **Infrastructure Metrics** (CPU, Memory, Disk, Network)
- **Application Metrics** (Request rates, Queue sizes, Lag)

---

## Monitoring Stack

### Components

```
┌─────────────────────────────────────────────────────────┐
│                     Applications                         │
│  (Expose /metrics endpoint - Prometheus format)         │
└────────────────────┬────────────────────────────────────┘
                     │
     ┌───────────────┴───────────────┐
     │                               │
┌────▼─────────┐            ┌───────▼──────┐
│ Prometheus   │            │ Promtail     │
│ (Metrics)    │            │ (Logs)       │
└────┬─────────┘            └───────┬──────┘
     │                               │
     │                        ┌──────▼──────┐
     │                        │ Loki        │
     │                        │ (Log Store) │
     │                        └──────┬──────┘
     │                               │
     └───────────────┬───────────────┘
                     │
              ┌──────▼──────┐
              │ Grafana     │
              │ (Dashboards)│
              └─────────────┘
```

### Starting Monitoring Stack

#### Development
```bash
cd cex/ops/docker
./scripts/up_dev.sh --with-monitoring
```

#### Kubernetes (Production)
```bash
kubectl apply -f ops/k8s/base/monitoring/
```

### Accessing Monitoring Tools

**Development:**
- Grafana: http://localhost:3100 (admin/admin)
- Prometheus: http://localhost:9090
- AlertManager: http://localhost:9093

**Production:**
- Grafana: https://grafana.animica.io
- Prometheus: https://prometheus.animica.io (internal only)
- AlertManager: https://alertmanager.animica.io (internal only)

---

## Metrics

### Application Metrics

All services expose Prometheus metrics at `/metrics` endpoint.

#### Standard Metrics

**HTTP Requests:**
```promql
# Total requests
http_requests_total

# Request duration (histogram)
http_request_duration_seconds

# Request size
http_request_size_bytes

# Response size
http_response_size_bytes
```

**WebSocket Connections:**
```promql
# Active connections
websocket_connections_active

# Total connections
websocket_connections_total

# Messages sent/received
websocket_messages_total{direction="sent|received"}
```

#### Business Metrics

**Orders:**
```promql
# Orders created
orders_created_total

# Orders matched
orders_matched_total

# Orders cancelled
orders_cancelled_total

# Active orders
orders_active
```

**Trades:**
```promql
# Trades executed
trades_executed_total

# Trade volume (USD)
trade_volume_usd

# Average trade size
trade_size_avg
```

**Deposits/Withdrawals:**
```promql
# Deposits processed
deposits_processed_total

# Withdrawals processed
withdrawals_processed_total

# Withdrawal queue size
withdrawals_queue_size

# Deposit confirmations
deposits_confirmations_avg
```

#### Service-Specific Metrics

**Matching Engine:**
```promql
# Event processing lag
matching_engine_event_lag_seconds

# Orders in orderbook
matching_engine_orderbook_orders

# Events processed
matching_engine_events_processed_total
```

**Ledger Service:**
```promql
# Reconciliation status
ledger_reconciliation_last_success_timestamp

# Reconciliation failures
ledger_reconciliation_failures_total

# Balance checks
ledger_balance_checks_total
```

**Animica Scanner:**
```promql
# Block lag
animica_indexer_block_lag

# Blocks scanned
animica_indexer_blocks_scanned_total

# Deposits detected
animica_indexer_deposits_detected_total
```

### Infrastructure Metrics

**PostgreSQL:**
```promql
# Connections
pg_stat_database_numbackends

# Query duration
pg_stat_statements_mean_exec_time_ms

# Database size
pg_database_size_bytes

# Transactions per second
rate(pg_stat_database_xact_commit[1m])
```

**Redis:**
```promql
# Memory usage
redis_memory_used_bytes
redis_memory_max_bytes

# Connections
redis_connected_clients

# Operations per second
rate(redis_commands_processed_total[1m])

# Evictions
redis_evicted_keys_total
```

**NATS:**
```promql
# Messages in stream
nats_stream_messages

# Message rate
rate(nats_stream_messages_total[1m])

# Consumer lag
nats_consumer_num_pending
```

### Querying Metrics

#### Via Prometheus UI

1. Open Prometheus: http://localhost:9090 (dev) or https://prometheus.animica.io (prod)
2. Enter query in search box
3. Click "Execute"
4. View graph or table

**Example Queries:**

```promql
# Request rate per service
sum(rate(http_requests_total[5m])) by (job)

# Error rate
sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))

# 95th percentile latency
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# Top 5 services by CPU
topk(5, avg by (pod) (rate(container_cpu_usage_seconds_total[5m])))
```

#### Via Grafana

1. Open Grafana dashboard
2. Click "Explore"
3. Select "Prometheus" data source
4. Enter PromQL query
5. Run query

#### Via API

```bash
# Query Prometheus API
curl 'http://localhost:9090/api/v1/query?query=up'

# Query with time range
curl 'http://localhost:9090/api/v1/query_range?query=rate(http_requests_total[5m])&start=2024-01-25T00:00:00Z&end=2024-01-25T23:59:59Z&step=60s'
```

---

## Alerts

### Alert Rules

Configured in `ops/monitoring/alerts/cex-alerts.yml`

#### Critical Alerts (P0)

**ServiceDown**
- **Condition**: Service unavailable for >2 minutes
- **Action**: Immediate response required
- **Runbook**: Check pod status, logs, restart if needed

**PostgresDown**
- **Condition**: Database unreachable for >1 minute
- **Action**: Critical - all services affected
- **Runbook**: Check postgres pod, storage, network

**HighErrorRate**
- **Condition**: Error rate >5% for >5 minutes
- **Action**: Investigate and resolve
- **Runbook**: Check service logs, recent deployments

**WithdrawalsProcessingStalled**
- **Condition**: No withdrawals processed in 15m with pending queue
- **Action**: Critical - user funds stuck
- **Runbook**: Check withdrawals-service, wallet-router logs

#### Warning Alerts (P1)

**OrderbookEventLag**
- **Condition**: Event processing lag >60s for >5 minutes
- **Action**: Monitor, scale if needed
- **Runbook**: Check matching-engine, NATS queue

**DepositScannerLag**
- **Condition**: Scanner >100 blocks behind for >10 minutes
- **Action**: Monitor, restart if stuck
- **Runbook**: Check animica-indexer, RPC connectivity

**PostgresDiskUsageHigh**
- **Condition**: Database size >80GB
- **Action**: Plan for capacity increase
- **Runbook**: Review data retention, scale storage

**RedisMemoryHigh**
- **Condition**: Redis using >90% of allocated memory
- **Action**: Review cache policies, scale if needed
- **Runbook**: Check Redis memory stats, review keys

### Alert Configuration

#### AlertManager Configuration

Located at `ops/monitoring/alertmanager-config.yml`

**Routing:**
```yaml
routes:
  - match:
      severity: critical
    receiver: 'critical'  # Email + PagerDuty
  - match:
      severity: warning
    receiver: 'warning'   # Email only
```

**Receivers:**
```yaml
receivers:
  - name: 'critical'
    email_configs:
      - to: 'oncall@cex.local'
    pagerduty_configs:
      - service_key: $PAGERDUTY_KEY
  - name: 'warning'
    email_configs:
      - to: 'team@cex.local'
```

#### Silencing Alerts

Temporarily silence alerts during maintenance:

```bash
# Via AlertManager UI
# 1. Open https://alertmanager.animica.io
# 2. Click "Silences"
# 3. Create new silence with:
#    - Matchers: alertname="ServiceDown", instance="api-gateway"
#    - Duration: 1h
#    - Comment: "Planned maintenance"

# Via API
curl -X POST https://alertmanager.animica.io/api/v2/silences \
  -H 'Content-Type: application/json' \
  -d '{
    "matchers": [
      {
        "name": "alertname",
        "value": "ServiceDown",
        "isRegex": false
      }
    ],
    "startsAt": "2024-01-25T00:00:00Z",
    "endsAt": "2024-01-25T01:00:00Z",
    "comment": "Planned maintenance",
    "createdBy": "operator@cex.local"
  }'
```

### Alert Testing

Test alert firing:

```bash
# Trigger test alert
curl -X POST http://localhost:9090/api/v1/admin/tsdb/delete_series \
  -d 'match[]=up{job="test"}'

# Or manually send alert to AlertManager
curl -X POST http://localhost:9093/api/v1/alerts \
  -H 'Content-Type: application/json' \
  -d '[
    {
      "labels": {
        "alertname": "TestAlert",
        "severity": "warning"
      },
      "annotations": {
        "summary": "Test alert"
      }
    }
  ]'
```

---

## Dashboards

### Pre-Built Dashboards

Located at `ops/monitoring/dashboards/`

#### Exchange Overview
- Service health (up/down status)
- Request rates and error rates
- Matching engine lag
- Withdrawal queue size
- Database connections
- Redis memory usage

**URL:** http://localhost:3100/d/exchange-overview

#### Infrastructure Dashboard
- CPU usage per service
- Memory usage per service
- Disk I/O
- Network traffic
- Database performance
- Cache hit rates

#### Business Metrics Dashboard
- Orders created/matched/cancelled
- Trade volume (24h, 7d, 30d)
- Active users
- Deposit/withdrawal counts
- Revenue (fees collected)

#### SLO Dashboard
- Request success rate (target: 99.9%)
- Request latency p50/p95/p99
- Availability per service
- Error budget remaining

### Creating Custom Dashboards

#### Via Grafana UI

1. Open Grafana
2. Click "+" → "Dashboard"
3. Add panel
4. Select data source (Prometheus)
5. Enter query
6. Configure visualization
7. Save dashboard

#### Via JSON

```json
{
  "dashboard": {
    "title": "My Custom Dashboard",
    "panels": [
      {
        "title": "Request Rate",
        "targets": [
          {
            "expr": "rate(http_requests_total[5m])",
            "legendFormat": "{{job}}"
          }
        ]
      }
    ]
  }
}
```

Save to `ops/monitoring/dashboards/` and Grafana will auto-load.

### Dashboard Best Practices

1. **Keep it Simple**: Focus on actionable metrics
2. **Use Variables**: Make dashboards reusable
3. **Add Annotations**: Mark deployments, incidents
4. **Set Thresholds**: Visual indicators for normal ranges
5. **Link Dashboards**: Create dashboard hierarchy

---

## Log Aggregation

### Loki Configuration

Logs are collected by Promtail and stored in Loki.

#### Log Sources

- Application logs (stdout/stderr from containers)
- System logs (syslog, kernel logs)
- Audit logs (access logs, admin actions)

#### Log Format

**JSON Format (Recommended):**
```json
{
  "timestamp": "2024-01-25T12:00:00Z",
  "level": "info",
  "service": "api-gateway",
  "message": "Request processed",
  "request_id": "req-123",
  "user_id": "user-456",
  "duration_ms": 45
}
```

**Structured Logging Benefits:**
- Easy filtering
- Consistent parsing
- Metadata extraction
- Correlation across services

#### Querying Logs

**Via Grafana Explore:**

1. Open Grafana → Explore
2. Select "Loki" data source
3. Enter LogQL query
4. Run query

**Example LogQL Queries:**

```logql
# All logs from api-gateway
{compose_service="api-gateway"}

# Error logs
{compose_service="api-gateway"} |= "error"

# JSON field filter
{compose_service="api-gateway"} | json | level="error"

# Rate of errors
rate({compose_service="api-gateway"} |= "error" [5m])

# Top error messages
topk(10, sum by (message) (rate({level="error"} [1h])))
```

**Via Loki API:**

```bash
# Query logs
curl -G -s "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={compose_service="api-gateway"}' \
  --data-urlencode 'start=2024-01-25T00:00:00Z' \
  --data-urlencode 'end=2024-01-25T23:59:59Z' \
  | jq
```

#### Log Retention

- **Default**: 30 days
- **Configured in**: `ops/monitoring/loki-config.yml`

```yaml
limits_config:
  retention_period: 720h  # 30 days
```

To change retention:
```bash
# Edit config
vim ops/monitoring/loki-config.yml

# Restart Loki
docker compose -f ops/docker/docker-compose.monitoring.yml restart loki
```

---

## Troubleshooting

### Metrics Not Appearing

**Symptoms:** No data in Prometheus/Grafana

**Checklist:**
- [ ] Service exposes /metrics endpoint
- [ ] Prometheus can reach service (network policy)
- [ ] Service is in Prometheus scrape config
- [ ] Service is healthy and running

**Debugging:**

```bash
# Test /metrics endpoint
curl http://api-gateway:3000/metrics

# Check Prometheus targets
curl http://localhost:9090/api/v1/targets | jq

# Check Prometheus config
curl http://localhost:9090/api/v1/status/config | jq

# Check Prometheus logs
kubectl logs deployment/prometheus -n cex-prod
```

### Alerts Not Firing

**Symptoms:** Expected alerts not triggering

**Checklist:**
- [ ] Alert rule is correct
- [ ] Metrics are being collected
- [ ] Alert threshold is reasonable
- [ ] AlertManager is configured
- [ ] Alert is not silenced

**Debugging:**

```bash
# Check alert rules
curl http://localhost:9090/api/v1/rules | jq

# Check active alerts
curl http://localhost:9090/api/v1/alerts | jq

# Check AlertManager
curl http://localhost:9093/api/v2/alerts | jq

# Test alert expression
curl -G 'http://localhost:9090/api/v1/query' \
  --data-urlencode 'query=up == 0' | jq
```

### Logs Not Showing

**Symptoms:** Logs missing from Loki

**Checklist:**
- [ ] Promtail is running
- [ ] Promtail can access Docker socket
- [ ] Loki is running and reachable
- [ ] Log labels are correct

**Debugging:**

```bash
# Check Promtail logs
kubectl logs deployment/promtail -n cex-prod

# Test Loki API
curl http://localhost:3100/ready

# Check Loki ingestion
curl http://localhost:3100/metrics | grep loki_ingester_chunks_created_total

# Manual log push (test)
curl -X POST http://localhost:3100/loki/api/v1/push \
  -H 'Content-Type: application/json' \
  -d '{
    "streams": [
      {
        "stream": {"job": "test"},
        "values": [
          ["'$(date +%s)000000000'", "test log line"]
        ]
      }
    ]
  }'
```

### High Cardinality Issues

**Symptoms:** Prometheus using excessive memory/CPU

**Cause:** Too many unique label combinations

**Resolution:**

```bash
# Find high cardinality metrics
curl http://localhost:9090/api/v1/label/__name__/values | jq '.data[]' | wc -l

# Check series count
curl http://localhost:9090/api/v1/status/tsdb | jq '.data.seriesCountByMetricName'

# Drop problematic metrics
# Edit prometheus.yml and add metric_relabel_configs
```

---

## Performance Monitoring

### Request Latency

Track request latency using histograms:

```promql
# p50 latency
histogram_quantile(0.5, rate(http_request_duration_seconds_bucket[5m]))

# p95 latency
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))

# p99 latency
histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))
```

**Latency SLO:** p95 < 200ms, p99 < 500ms

### Throughput

Track requests per second:

```promql
# Total RPS
sum(rate(http_requests_total[5m]))

# RPS by service
sum(rate(http_requests_total[5m])) by (job)

# RPS by endpoint
sum(rate(http_requests_total[5m])) by (path)
```

### Error Rate

Track error percentage:

```promql
# Overall error rate
sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m])) * 100

# Error rate by service
sum(rate(http_requests_total{status=~"5.."}[5m])) by (job) / sum(rate(http_requests_total[5m])) by (job) * 100
```

**Error SLO:** < 0.1% (99.9% success rate)

### Capacity Monitoring

Track resource utilization:

```promql
# CPU usage
sum(rate(container_cpu_usage_seconds_total[5m])) by (pod)

# Memory usage
sum(container_memory_working_set_bytes) by (pod)

# Disk usage
node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"} * 100
```

---

## Best Practices

1. **Instrument Everything**: Add metrics to all code paths
2. **Use Standard Metrics**: Follow Prometheus naming conventions
3. **Keep Labels Low Cardinality**: Avoid user IDs, request IDs in labels
4. **Add Context**: Include metadata in logs (request_id, user_id)
5. **Monitor Business Metrics**: Not just infrastructure
6. **Set Meaningful Alerts**: Avoid alert fatigue
7. **Document Runbooks**: Link alerts to resolution procedures
8. **Test Monitoring**: Verify alerts work before incidents
9. **Review Regularly**: Update dashboards and alerts as system evolves
10. **Correlate Data**: Use same request_id in metrics and logs

---

**Document Version**: 1.0  
**Last Updated**: 2024-01-25  
**Maintained By**: CEX Infrastructure Team
