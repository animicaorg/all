# CEX Operational Runbook

## Table of Contents
1. [Quick Reference](#quick-reference)
2. [Development Environment](#development-environment)
3. [Production Operations](#production-operations)
4. [Database Operations](#database-operations)
5. [Incident Response](#incident-response)
6. [Monitoring & Alerts](#monitoring--alerts)
7. [Troubleshooting](#troubleshooting)
8. [On-Call Guide](#on-call-guide)

---

## Quick Reference

### Emergency Contacts
- **On-Call Engineer**: [Configure in AlertManager]
- **Team Lead**: [Configure in AlertManager]
- **Infrastructure**: [Configure in AlertManager]

### Critical URLs
- **Production API**: https://api.animica.io
- **Admin Panel**: https://admin.animica.io
- **Grafana**: https://grafana.animica.io
- **Prometheus**: https://prometheus.animica.io

### Quick Commands
```bash
# Check service health
curl https://api.animica.io/health

# View logs
kubectl logs -f deployment/api-gateway -n cex-prod

# Restart service
kubectl rollout restart deployment/api-gateway -n cex-prod

# Scale service
kubectl scale deployment/api-gateway --replicas=5 -n cex-prod
```

---

## Development Environment

### Starting the Dev Stack

#### Prerequisites
- Docker Desktop or Docker Engine (v20+)
- Docker Compose V2
- At least 8GB RAM available
- At least 20GB disk space

#### One-Command Startup
```bash
cd cex/ops/docker
./scripts/up_dev.sh
```

This script will:
1. ✅ Check prerequisites
2. 📋 Copy environment file if missing
3. 🚀 Start infrastructure (Postgres, Redis, NATS, MinIO, MailHog)
4. ⏳ Wait for services to be healthy
5. 🗄️ Run database migrations
6. 🌱 Seed initial data (optional)
7. 🎯 Start all application services
8. 🧪 Run smoke tests
9. 📊 Optionally start monitoring stack (--with-monitoring flag)

#### With Monitoring
```bash
./scripts/up_dev.sh --with-monitoring
```

### Stopping the Dev Stack
```bash
cd cex/ops/docker
./scripts/down_dev.sh
```

### Viewing Logs
```bash
# View all logs
docker compose -f ops/docker/docker-compose.dev.yml logs -f

# View specific service logs
./scripts/logs.sh api-gateway --follow

# View last 100 lines
./scripts/logs.sh matching-engine --tail 100
```

### Development URLs
- **API Gateway**: http://localhost:3000
- **Admin Service**: http://localhost:3001
- **BitGo Webhook**: http://localhost:3002
- **PostgreSQL**: localhost:5432
- **Redis**: localhost:6379
- **NATS**: localhost:4222 (client), http://localhost:8222 (monitor)
- **MailHog UI**: http://localhost:8025
- **MinIO Console**: http://localhost:9001
- **Grafana** (if enabled): http://localhost:3100
- **Prometheus** (if enabled): http://localhost:9090

---

## Production Operations

### Kubernetes Deployment

#### Prerequisites
- kubectl configured with cluster access
- kustomize installed
- Access to Docker registry
- Secrets configured in cluster

#### Initial Deployment

1. **Create Namespace and Secrets**
```bash
# Create namespace
kubectl apply -f ops/k8s/base/namespace.yaml

# Create secrets (DO NOT use example file as-is!)
kubectl create secret generic cex-secrets \
  --from-literal=db-password='YOUR_SECURE_PASSWORD' \
  --from-literal=redis-password='YOUR_REDIS_PASSWORD' \
  --from-literal=jwt-secret='YOUR_JWT_SECRET' \
  --from-literal=session-secret='YOUR_SESSION_SECRET' \
  --from-literal=bitgo-access-token='YOUR_BITGO_TOKEN' \
  --from-literal=bitgo-webhook-secret='YOUR_WEBHOOK_SECRET' \
  --from-literal=admin-api-key='YOUR_ADMIN_KEY' \
  --from-literal=smtp-user='YOUR_SMTP_USER' \
  --from-literal=smtp-password='YOUR_SMTP_PASSWORD' \
  --from-literal=s3-access-key='YOUR_S3_ACCESS_KEY' \
  --from-literal=s3-secret-key='YOUR_S3_SECRET_KEY' \
  --namespace=cex-prod
```

2. **Deploy Infrastructure**
```bash
# Deploy stateful services first
kubectl apply -f ops/k8s/base/postgres/
kubectl apply -f ops/k8s/base/redis/
kubectl apply -f ops/k8s/base/nats/

# Wait for infrastructure to be ready
kubectl wait --for=condition=ready pod -l app=postgres -n cex-prod --timeout=300s
kubectl wait --for=condition=ready pod -l app=redis -n cex-prod --timeout=120s
kubectl wait --for=condition=ready pod -l app=nats -n cex-prod --timeout=120s
```

3. **Run Database Migrations**
```bash
# Create migration job
kubectl apply -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: db-migrate-$(date +%Y%m%d-%H%M%S)
  namespace: cex-prod
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: migrate
        image: registry.animica.io/cex-migrations:latest
        command: ["pnpm", "migrate"]
        envFrom:
        - configMapRef:
            name: cex-config
        - secretRef:
            name: cex-secrets
EOF

# Watch migration job
kubectl logs -f job/db-migrate-TIMESTAMP -n cex-prod
```

4. **Deploy Application Services**
```bash
# Deploy using kustomize (production overlay)
kubectl apply -k ops/k8s/overlays/prod/

# Or deploy specific service
kubectl apply -f ops/k8s/base/services/api-gateway.yaml

# Verify deployment
kubectl get deployments -n cex-prod
kubectl get pods -n cex-prod
```

5. **Deploy Network Policies**
```bash
kubectl apply -f ops/k8s/base/networkpolicies/
```

6. **Deploy Ingress**
```bash
kubectl apply -f ops/k8s/base/ingress/
```

### Rolling Updates

#### Safe Deployment Process
```bash
# 1. Build and push new image
docker build -t registry.animica.io/cex-api-gateway:v1.2.3 .
docker push registry.animica.io/cex-api-gateway:v1.2.3

# 2. Update deployment image
kubectl set image deployment/api-gateway \
  api-gateway=registry.animica.io/cex-api-gateway:v1.2.3 \
  -n cex-prod

# 3. Watch rollout
kubectl rollout status deployment/api-gateway -n cex-prod

# 4. Verify new pods are healthy
kubectl get pods -l app=api-gateway -n cex-prod
kubectl logs -f deployment/api-gateway -n cex-prod

# 5. Run smoke tests
curl -f https://api.animica.io/health || echo "HEALTH CHECK FAILED"
```

### Rollback Procedure
```bash
# Rollback to previous version
kubectl rollout undo deployment/api-gateway -n cex-prod

# Rollback to specific revision
kubectl rollout history deployment/api-gateway -n cex-prod
kubectl rollout undo deployment/api-gateway --to-revision=2 -n cex-prod

# Verify rollback
kubectl rollout status deployment/api-gateway -n cex-prod
```

### Scaling Services

#### Manual Scaling
```bash
# Scale up
kubectl scale deployment/api-gateway --replicas=10 -n cex-prod

# Scale down
kubectl scale deployment/api-gateway --replicas=3 -n cex-prod

# Verify
kubectl get deployment api-gateway -n cex-prod
```

#### Auto-Scaling (HPA)
HPA is configured for api-gateway and will automatically scale based on CPU/memory.
```bash
# View HPA status
kubectl get hpa -n cex-prod

# Describe HPA
kubectl describe hpa api-gateway-hpa -n cex-prod

# Temporarily disable HPA
kubectl delete hpa api-gateway-hpa -n cex-prod

# Re-enable
kubectl apply -f ops/k8s/base/services/api-gateway.yaml
```

---

## Database Operations

### Running Migrations

#### Development
```bash
cd cex/ops/docker
./scripts/migrate.sh
```

#### Production (Kubernetes)
```bash
# Create one-time migration job
kubectl create job --from=cronjob/db-migrate db-migrate-manual -n cex-prod

# Monitor
kubectl logs -f job/db-migrate-manual -n cex-prod

# Cleanup
kubectl delete job db-migrate-manual -n cex-prod
```

### Migration Checklist

**Pre-Migration:**
- [ ] Review migration SQL/code
- [ ] Create database backup
- [ ] Verify backup integrity
- [ ] Put system in maintenance mode (if necessary)
- [ ] Notify team

**Migration:**
- [ ] Run migration in transaction if possible
- [ ] Monitor for errors
- [ ] Verify migration version updated

**Post-Migration:**
- [ ] Verify table structure
- [ ] Run smoke tests
- [ ] Check application logs
- [ ] Remove maintenance mode
- [ ] Notify team of completion

**Rollback (if needed):**
- [ ] Stop services
- [ ] Restore from backup
- [ ] Verify data integrity
- [ ] Restart services
- [ ] Post-mortem

### Backup and Restore

#### Manual Backup (Development)
```bash
cd cex/ops/docker
./scripts/backup_postgres.sh
```

#### Manual Backup (Production)
```bash
# Trigger backup job manually
kubectl create job --from=cronjob/postgres-backup postgres-backup-manual -n cex-prod

# Monitor
kubectl logs -f job/postgres-backup-manual -n cex-prod

# Download backup from S3
aws s3 cp s3://animica-cex-prod-backups/postgres/postgres_backup_20240115_020000.sql.gz ./
```

#### Restore from Backup (Development)
```bash
cd cex/ops/docker
./scripts/restore_postgres.sh /path/to/backup.sql.gz
```

#### Restore from Backup (Production)
⚠️ **CRITICAL OPERATION - REQUIRES APPROVAL**

```bash
# 1. Stop all application services
kubectl scale deployment --all --replicas=0 -n cex-prod

# 2. Download backup
aws s3 cp s3://animica-cex-prod-backups/postgres/postgres_backup_TIMESTAMP.sql.gz ./

# 3. Verify checksum
aws s3 cp s3://animica-cex-prod-backups/postgres/postgres_backup_TIMESTAMP.sql.gz.sha256 ./
sha256sum -c postgres_backup_TIMESTAMP.sql.gz.sha256

# 4. Restore to database
kubectl exec -it postgres-0 -n cex-prod -- bash
# Inside pod:
gunzip < /path/to/backup.sql.gz | psql -U $POSTGRES_USER $POSTGRES_DB

# 5. Verify restore
kubectl exec -it postgres-0 -n cex-prod -- psql -U cex_prod cex_exchange -c "\dt"

# 6. Restart services
kubectl scale deployment --all --replicas=1 -n cex-prod  # Adjust replicas as needed
```

### Database Performance

#### Check Connection Pool
```bash
# Development
docker compose -f ops/docker/docker-compose.dev.yml exec postgres \
  psql -U cex -d cex_exchange -c "SELECT count(*) FROM pg_stat_activity;"

# Production
kubectl exec -it postgres-0 -n cex-prod -- \
  psql -U cex_prod -d cex_exchange -c "SELECT count(*) FROM pg_stat_activity;"
```

#### Identify Slow Queries
```bash
kubectl exec -it postgres-0 -n cex-prod -- \
  psql -U cex_prod -d cex_exchange -c "
    SELECT pid, now() - pg_stat_activity.query_start AS duration, query
    FROM pg_stat_activity
    WHERE state = 'active' AND now() - pg_stat_activity.query_start > interval '5 seconds'
    ORDER BY duration DESC;
  "
```

#### Kill Long-Running Query
```bash
kubectl exec -it postgres-0 -n cex-prod -- \
  psql -U cex_prod -d cex_exchange -c "SELECT pg_terminate_backend(PID);"
```

---

## Incident Response

### P0: Service Down

**Immediate Actions:**
1. Check service health dashboard in Grafana
2. View recent logs for errors
3. Check resource usage (CPU, memory, disk)
4. Verify infrastructure (DB, Redis, NATS) is healthy
5. Scale up if resource constrained
6. Restart service if necessary

**Commands:**
```bash
# Check pod status
kubectl get pods -n cex-prod

# View logs
kubectl logs -f deployment/api-gateway --tail=100 -n cex-prod

# Describe pod for events
kubectl describe pod <pod-name> -n cex-prod

# Check resource usage
kubectl top pods -n cex-prod

# Restart deployment
kubectl rollout restart deployment/api-gateway -n cex-prod
```

### P1: High Error Rate

**Investigation Steps:**
1. Check Prometheus metrics for error rate
2. Review application logs for error patterns
3. Check database health and slow queries
4. Verify external dependencies (BitGo, Animica RPC)
5. Check rate limiting or DDoS

**Commands:**
```bash
# View error logs
kubectl logs deployment/api-gateway -n cex-prod | grep -i error | tail -50

# Check metrics
curl https://api.animica.io/metrics | grep http_requests_total
```

### P1: Matching Engine Lag

**Symptoms:** Orders not matching, high event lag metric

**Actions:**
1. Check matching-engine pod status and logs
2. Verify NATS is healthy and not backlogged
3. Check orderbook event queue size
4. Scale matching-engine if CPU/memory constrained
5. Consider temporary trading halt if lag critical

```bash
# Check matching engine status
kubectl get pods -l app=matching-engine -n cex-prod
kubectl logs -f deployment/matching-engine -n cex-prod --tail=100

# Check NATS queue
kubectl exec -it nats-0 -n cex-prod -- nats stream info ORDERBOOK

# Scale if needed (carefully - stateful service)
kubectl scale deployment/matching-engine --replicas=2 -n cex-prod
```

### P1: Withdrawals Stuck

**Symptoms:** Withdrawals in pending_approval for too long

**Actions:**
1. Check withdrawals-service logs
2. Verify wallet-router connectivity to BitGo
3. Check for failed BitGo webhooks
4. Review risk-service for blocks
5. Manual approval via admin panel if needed

```bash
# Check withdrawals service
kubectl logs -f deployment/withdrawals-service -n cex-prod

# Check wallet router
kubectl logs -f deployment/wallet-router -n cex-prod

# Query stuck withdrawals (via admin tool or direct DB)
kubectl exec -it postgres-0 -n cex-prod -- \
  psql -U cex_prod -d cex_exchange -c "
    SELECT id, user_id, amount, status, created_at
    FROM withdrawals
    WHERE status = 'pending_approval' AND created_at < NOW() - INTERVAL '30 minutes'
    LIMIT 10;
  "
```

### P2: Scanner Lag

**Symptoms:** Deposit scanner blocks behind chain tip

**Actions:**
1. Check animica-indexer logs for errors
2. Verify Animica RPC connectivity
3. Check database write performance
4. Restart scanner if stuck

```bash
# Check indexer
kubectl logs -f deployment/animica-indexer -n cex-prod --tail=100

# Check block lag metric
curl https://api.animica.io/metrics | grep animica_indexer_block_lag

# Restart if stuck
kubectl rollout restart deployment/animica-indexer -n cex-prod
```

### Administrative Actions

#### Halt Market Trading
```bash
# Via admin API (requires admin API key)
curl -X POST https://admin.animica.io/api/admin/market/halt \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"market_id": "BTC-USD", "reason": "Emergency maintenance"}'
```

#### Pause All Withdrawals
```bash
curl -X POST https://admin.animica.io/api/admin/withdrawals/pause \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -d '{"reason": "Security incident"}'
```

#### Freeze User Account
```bash
curl -X POST https://admin.animica.io/api/admin/users/$USER_ID/freeze \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -d '{"reason": "Suspicious activity"}'
```

#### Cancel All Orders for User
```bash
curl -X POST https://admin.animica.io/api/admin/orders/cancel-all \
  -H "Authorization: Bearer $ADMIN_API_KEY" \
  -d '{"user_id": "$USER_ID", "reason": "Admin action"}'
```

---

## Monitoring & Alerts

### Alert Severity Levels

**Critical (P0):**
- Service down
- Database down
- Redis down
- NATS down
- High error rate (>10%)
- Matching engine lag >5 minutes
- Withdrawals processing stalled

**Warning (P1):**
- High CPU usage (>80%)
- High memory usage (>80%)
- Disk space low (<20%)
- Error rate elevated (>5%)
- Matching engine lag >1 minute
- Scanner lag >100 blocks
- Slow queries (>1s average)

**Info:**
- Deployment events
- Scaling events
- Backup completion

### Accessing Monitoring

- **Grafana**: https://grafana.animica.io
- **Prometheus**: https://prometheus.animica.io
- **AlertManager**: https://alertmanager.animica.io

### Key Dashboards

1. **Exchange Overview**: Service health, request rates, error rates
2. **Infrastructure**: Database, Redis, NATS metrics
3. **Matching Engine**: Event lag, order rates, trade volume
4. **Withdrawals**: Queue size, processing rate, approvals
5. **System**: CPU, memory, disk, network

### Common Queries

```promql
# Request rate by service
rate(http_requests_total[5m])

# Error rate
rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m])

# Matching engine event lag
matching_engine_event_lag_seconds

# Withdrawal queue size
withdrawals_queue_size

# Database connections
pg_stat_database_numbackends

# Redis memory usage
redis_memory_used_bytes / redis_memory_max_bytes
```

---

## Troubleshooting

### Service Won't Start

**Symptoms:** Pod in CrashLoopBackOff

**Checklist:**
- [ ] Check logs for errors
- [ ] Verify environment variables are set
- [ ] Check secrets are mounted correctly
- [ ] Verify database connection
- [ ] Check resource limits
- [ ] Verify image exists and is accessible

```bash
kubectl logs <pod-name> -n cex-prod --previous
kubectl describe pod <pod-name> -n cex-prod
kubectl get events -n cex-prod --sort-by='.lastTimestamp'
```

### Database Connection Issues

**Symptoms:** Services can't connect to Postgres

**Checklist:**
- [ ] Verify Postgres pod is running
- [ ] Check Postgres logs
- [ ] Verify service can resolve postgres hostname
- [ ] Check credentials in secrets
- [ ] Verify network policies allow connection

```bash
# Check Postgres pod
kubectl get pods -l app=postgres -n cex-prod

# Test DNS resolution
kubectl run -it --rm debug --image=busybox --restart=Never -n cex-prod -- nslookup postgres

# Test connection
kubectl run -it --rm debug --image=postgres:16 --restart=Never -n cex-prod -- \
  psql -h postgres -U cex_prod -d cex_exchange -c "SELECT 1;"
```

### NATS Queue Backlog

**Symptoms:** Messages accumulating, not being processed

**Checklist:**
- [ ] Check consumer services are running
- [ ] Verify consumers are connected to NATS
- [ ] Check for errors in consumer logs
- [ ] Scale up consumers if needed

```bash
# Check NATS streams
kubectl exec -it nats-0 -n cex-prod -- nats stream list
kubectl exec -it nats-0 -n cex-prod -- nats stream info ORDERBOOK

# Check consumers
kubectl exec -it nats-0 -n cex-prod -- nats consumer list ORDERBOOK
```

### High Memory Usage

**Symptoms:** Service getting OOMKilled

**Actions:**
1. Check memory metrics in Grafana
2. Review application logs for memory leaks
3. Increase memory limits temporarily
4. Investigate and fix root cause

```bash
# Check current usage
kubectl top pods -n cex-prod

# Increase memory limit
kubectl patch deployment api-gateway -n cex-prod -p \
  '{"spec":{"template":{"spec":{"containers":[{"name":"api-gateway","resources":{"limits":{"memory":"1Gi"}}}]}}}}'
```

---

## On-Call Guide

### On-Call Responsibilities

- Monitor alerts (email, Slack, PagerDuty)
- Respond to P0/P1 incidents within SLA
- Escalate if unable to resolve
- Document incidents and actions taken
- Update runbook with learnings

### SLAs

- **P0 (Critical)**: Response time 15 minutes, resolution target 1 hour
- **P1 (High)**: Response time 30 minutes, resolution target 4 hours
- **P2 (Medium)**: Response time 4 hours, resolution target 24 hours

### On-Call Checklist

**Before Shift:**
- [ ] Test access to all systems (kubectl, AWS, Grafana)
- [ ] Review current system status
- [ ] Check for scheduled maintenance
- [ ] Review recent incidents

**During Shift:**
- [ ] Monitor alerts actively
- [ ] Respond to incidents promptly
- [ ] Document all actions taken
- [ ] Update team on significant events

**After Shift:**
- [ ] Hand off any ongoing incidents
- [ ] Document lessons learned
- [ ] Update runbook if needed
- [ ] Brief next on-call engineer

### Escalation Path

1. **Level 1**: On-call engineer
2. **Level 2**: Team lead
3. **Level 3**: Engineering manager
4. **Level 4**: CTO

### Emergency Contacts

Keep contact information for:
- Infrastructure team
- Database administrator
- Security team
- External vendors (BitGo, cloud provider)

---

## Additional Resources

- [Deployment Guide](./deployment.md)
- [Backup & Recovery Guide](./backups.md)
- [Monitoring Guide](./monitoring.md)
- [Architecture Documentation](./architecture.md)
- [Security Baseline](./security_baseline.md)

---

**Document Version**: 1.0  
**Last Updated**: 2024-01-25  
**Maintained By**: CEX Infrastructure Team
