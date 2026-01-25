# CEX Backup & Recovery Guide

## Table of Contents
1. [Overview](#overview)
2. [Backup Strategy](#backup-strategy)
3. [PostgreSQL Backups](#postgresql-backups)
4. [Recovery Procedures](#recovery-procedures)
5. [Disaster Recovery](#disaster-recovery)
6. [Testing & Validation](#testing--validation)

---

## Overview

This document outlines the backup and recovery procedures for the CEX (Centralized Exchange) platform.

### Recovery Objectives

- **RPO (Recovery Point Objective)**: 1 hour
  - Maximum acceptable data loss: 1 hour of transactions
  - Achieved through: Continuous WAL archiving + hourly backups

- **RTO (Recovery Time Objective)**: 2 hours
  - Maximum acceptable downtime: 2 hours
  - Time to restore from backup and bring system online

### Critical Data

**Priority 1 (Must backup):**
- PostgreSQL database (all user data, orders, transactions)
- Application secrets and keys
- Configuration files

**Priority 2 (Should backup):**
- Redis data (cache can be rebuilt)
- NATS persistent streams
- Application logs

**Priority 3 (Nice to backup):**
- Monitoring data (Prometheus, Grafana)
- Temporary files

---

## Backup Strategy

### Backup Types

1. **Full Backups** (PostgreSQL)
   - Frequency: Daily at 2:00 AM UTC
   - Retention: 30 days
   - Method: pg_dump with gzip compression
   - Storage: S3 bucket with versioning

2. **Incremental Backups** (WAL Archives)
   - Frequency: Continuous
   - Retention: 7 days
   - Method: PostgreSQL WAL archiving
   - Storage: S3 bucket

3. **Snapshot Backups** (Kubernetes)
   - Frequency: Weekly
   - Retention: 4 weeks
   - Method: Volume snapshots
   - Storage: Cloud provider snapshots

### Backup Locations

**Primary Storage:**
- S3 Bucket: `s3://animica-cex-prod-backups/`
- Structure:
  ```
  postgres/
    ├── daily/postgres_backup_20240125_020000.sql.gz
    ├── daily/postgres_backup_20240125_020000.sql.gz.sha256
    └── wal/
        ├── 000000010000000000000001
        └── 000000010000000000000002
  redis/
    └── dump.rdb
  configs/
    └── secrets-backup-20240125.enc
  ```

**Secondary Storage (DR):**
- S3 Bucket in different region: `s3://animica-cex-prod-backups-dr/`
- Replication: Cross-region replication enabled

---

## PostgreSQL Backups

### Automated Daily Backups

#### Development Environment

```bash
cd cex/ops/docker

# Manual backup
./scripts/backup_postgres.sh

# Scheduled backup (cron example)
# Add to crontab: 0 2 * * * /path/to/backup_postgres.sh
```

#### Production (Kubernetes)

Automated via CronJob:

```bash
# View backup schedule
kubectl get cronjob postgres-backup -n cex-prod

# View backup history
kubectl get jobs -n cex-prod | grep postgres-backup

# View latest backup logs
kubectl logs $(kubectl get pods -n cex-prod -l job-name=postgres-backup-28400960 --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}') -n cex-prod
```

Manual backup:
```bash
# Trigger manual backup
kubectl create job --from=cronjob/postgres-backup postgres-backup-manual -n cex-prod

# Monitor
kubectl logs -f job/postgres-backup-manual -n cex-prod

# Cleanup
kubectl delete job postgres-backup-manual -n cex-prod
```

### Backup Script Details

The backup script (`ops/docker/scripts/backup_postgres.sh`) performs:

1. **Dump Database**
   ```bash
   pg_dump -U $DB_USER $DB_NAME | gzip > $BACKUP_FILE
   ```

2. **Generate Checksum**
   ```bash
   sha256sum $BACKUP_FILE > ${BACKUP_FILE}.sha256
   ```

3. **Upload to S3**
   ```bash
   aws s3 cp $BACKUP_FILE s3://$S3_BUCKET/postgres/daily/
   aws s3 cp ${BACKUP_FILE}.sha256 s3://$S3_BUCKET/postgres/daily/
   ```

4. **Verify Upload**
   ```bash
   aws s3 ls s3://$S3_BUCKET/postgres/daily/$(basename $BACKUP_FILE)
   ```

5. **Cleanup Old Backups** (Retention: 30 days)
   ```bash
   aws s3 ls s3://$S3_BUCKET/postgres/daily/ | awk '{print $4}' | \
     while read file; do
       # Delete files older than 30 days
     done
   ```

### WAL Archiving (Continuous Backup)

For point-in-time recovery, enable WAL archiving:

#### Configuration

Add to PostgreSQL configuration:
```sql
-- postgresql.conf
wal_level = replica
archive_mode = on
archive_command = 'aws s3 cp %p s3://animica-cex-prod-backups/postgres/wal/%f'
archive_timeout = 300  # 5 minutes
```

#### Verification

```bash
# Check WAL archiving status
kubectl exec -it postgres-0 -n cex-prod -- \
  psql -U cex_prod -d cex_exchange -c "SELECT * FROM pg_stat_archiver;"

# List archived WAL files
aws s3 ls s3://animica-cex-prod-backups/postgres/wal/
```

### List Available Backups

```bash
# List all backups
aws s3 ls s3://animica-cex-prod-backups/postgres/daily/ --human-readable

# Find latest backup
aws s3 ls s3://animica-cex-prod-backups/postgres/daily/ | tail -1

# Download backup
aws s3 cp s3://animica-cex-prod-backups/postgres/daily/postgres_backup_20240125_020000.sql.gz ./
aws s3 cp s3://animica-cex-prod-backups/postgres/daily/postgres_backup_20240125_020000.sql.gz.sha256 ./
```

---

## Recovery Procedures

### Full Database Restore

⚠️ **WARNING: This is a destructive operation. All current data will be lost.**

#### Development Environment

```bash
cd cex/ops/docker

# Download backup
aws s3 cp s3://animica-cex-prod-backups/postgres/daily/postgres_backup_20240125_020000.sql.gz ./

# Restore
./scripts/restore_postgres.sh ./postgres_backup_20240125_020000.sql.gz
```

The restore script will:
1. Create a backup of current database
2. Drop and recreate database
3. Restore from backup file
4. Verify restoration
5. Provide rollback option if restore fails

#### Production (Kubernetes)

**Prerequisites:**
- Approval from team lead or CTO
- Incident documented
- Backup file verified

**Steps:**

1. **Stop All Services**
```bash
# Scale down all application deployments
kubectl scale deployment --all --replicas=0 -n cex-prod

# Verify all services stopped
kubectl get pods -n cex-prod
```

2. **Download and Verify Backup**
```bash
# Download backup
aws s3 cp s3://animica-cex-prod-backups/postgres/daily/postgres_backup_20240125_020000.sql.gz ./
aws s3 cp s3://animica-cex-prod-backups/postgres/daily/postgres_backup_20240125_020000.sql.gz.sha256 ./

# Verify checksum
sha256sum -c postgres_backup_20240125_020000.sql.gz.sha256
```

3. **Create Current Backup** (Safety)
```bash
kubectl exec -it postgres-0 -n cex-prod -- \
  pg_dump -U cex_prod cex_exchange | gzip > pre-restore-$(date +%Y%m%d_%H%M%S).sql.gz
```

4. **Restore Database**
```bash
# Copy backup to pod
kubectl cp postgres_backup_20240125_020000.sql.gz postgres-0:/tmp/ -n cex-prod

# Connect to pod and restore
kubectl exec -it postgres-0 -n cex-prod -- bash

# Inside pod:
# Drop connections
psql -U cex_prod -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'cex_exchange' AND pid <> pg_backend_pid();"

# Drop and recreate database
psql -U cex_prod -d postgres -c "DROP DATABASE cex_exchange;"
psql -U cex_prod -d postgres -c "CREATE DATABASE cex_exchange;"

# Restore
gunzip < /tmp/postgres_backup_20240125_020000.sql.gz | psql -U cex_prod -d cex_exchange

# Verify
psql -U cex_prod -d cex_exchange -c "SELECT COUNT(*) FROM users;"
psql -U cex_prod -d cex_exchange -c "SELECT COUNT(*) FROM orders;"
```

5. **Verify Database State**
```bash
# Check table counts
kubectl exec -it postgres-0 -n cex-prod -- \
  psql -U cex_prod -d cex_exchange -c "\dt+"

# Check migration version
kubectl exec -it postgres-0 -n cex-prod -- \
  psql -U cex_prod -d cex_exchange -c "SELECT version FROM migrations ORDER BY version DESC LIMIT 1;"
```

6. **Restart Services**
```bash
# Scale up services
kubectl scale deployment/api-gateway --replicas=3 -n cex-prod
kubectl scale deployment/matching-engine --replicas=1 -n cex-prod
kubectl scale deployment/ledger-service --replicas=1 -n cex-prod
# ... scale other services

# Or use previous replica counts
kubectl get deployment -n cex-prod
```

7. **Verify System Health**
```bash
# Check pod status
kubectl get pods -n cex-prod

# Test endpoints
curl -f https://api.animica.io/health
curl -f https://admin.animica.io/health

# Check logs for errors
kubectl logs deployment/api-gateway -n cex-prod --tail=50
```

### Point-in-Time Recovery (PITR)

Using WAL archives to recover to a specific timestamp:

```bash
# 1. Restore base backup
gunzip < postgres_backup_20240125_020000.sql.gz | psql -U cex_prod -d cex_exchange

# 2. Create recovery.conf
cat > /var/lib/postgresql/data/recovery.conf <<EOF
restore_command = 'aws s3 cp s3://animica-cex-prod-backups/postgres/wal/%f %p'
recovery_target_time = '2024-01-25 14:30:00'
recovery_target_action = 'promote'
EOF

# 3. Restart PostgreSQL
pg_ctl restart

# 4. Monitor recovery
tail -f /var/lib/postgresql/data/log/postgresql.log
```

### Partial Data Recovery

To recover specific tables without full restore:

```bash
# 1. Create temporary database
kubectl exec -it postgres-0 -n cex-prod -- \
  psql -U cex_prod -d postgres -c "CREATE DATABASE temp_restore;"

# 2. Restore to temporary database
kubectl exec -it postgres-0 -n cex-prod -- bash
gunzip < /tmp/backup.sql.gz | psql -U cex_prod -d temp_restore

# 3. Extract specific table data
psql -U cex_prod -d temp_restore -c "COPY users TO '/tmp/users_backup.csv' CSV HEADER;"

# 4. Import to production database
psql -U cex_prod -d cex_exchange -c "COPY users FROM '/tmp/users_backup.csv' CSV HEADER;"

# 5. Cleanup
psql -U cex_prod -d postgres -c "DROP DATABASE temp_restore;"
```

---

## Disaster Recovery

### Full System Recovery

In case of complete infrastructure failure:

#### Phase 1: Infrastructure Setup (30 minutes)

1. **Provision New Cluster**
   - Create Kubernetes cluster
   - Configure networking
   - Install ingress controller
   - Install cert-manager

2. **Setup Storage**
   - Create storage classes
   - Configure persistent volumes

3. **Configure Access**
   - Setup kubectl access
   - Configure registry access

#### Phase 2: Data Recovery (60 minutes)

1. **Deploy Infrastructure**
```bash
kubectl apply -f ops/k8s/base/namespace.yaml
kubectl apply -f ops/k8s/base/postgres/
kubectl apply -f ops/k8s/base/redis/
kubectl apply -f ops/k8s/base/nats/

# Wait for readiness
kubectl wait --for=condition=ready pod -l app=postgres -n cex-prod --timeout=300s
```

2. **Restore Database**
```bash
# Download latest backup from DR bucket
aws s3 cp s3://animica-cex-prod-backups-dr/postgres/daily/latest.sql.gz ./

# Restore (see Full Database Restore section)
```

3. **Restore Secrets**
```bash
# Restore from secure backup
kubectl apply -f secrets-backup-encrypted.yaml
```

#### Phase 3: Application Deployment (30 minutes)

1. **Deploy Services**
```bash
kubectl apply -k ops/k8s/overlays/prod/
```

2. **Verify Health**
```bash
kubectl get pods -n cex-prod
./scripts/smoke_test.sh
```

3. **Update DNS** (if needed)
   - Point domain to new cluster's load balancer

### DR Drills

Conduct quarterly DR drills:

**Drill Procedure:**
1. Schedule drill with team
2. Document start time
3. Follow disaster recovery procedure
4. Measure actual RTO
5. Document issues encountered
6. Update procedures
7. Share results with team

**Drill Checklist:**
- [ ] Drill scheduled
- [ ] Team notified
- [ ] Backup verified accessible
- [ ] Recovery procedure executed
- [ ] RTO measured
- [ ] Issues documented
- [ ] Procedures updated
- [ ] Post-drill review completed

---

## Testing & Validation

### Backup Validation

#### Automated Validation
```bash
# Test restore to temporary database
./scripts/test_backup_restore.sh postgres_backup_20240125_020000.sql.gz
```

#### Manual Validation
```bash
# 1. Download backup
aws s3 cp s3://animica-cex-prod-backups/postgres/daily/latest.sql.gz ./

# 2. Verify checksum
aws s3 cp s3://animica-cex-prod-backups/postgres/daily/latest.sql.gz.sha256 ./
sha256sum -c latest.sql.gz.sha256

# 3. Test restore to dev environment
docker compose -f ops/docker/docker-compose.dev.yml up -d postgres
./scripts/restore_postgres.sh latest.sql.gz

# 4. Verify data integrity
docker compose exec postgres psql -U cex -d cex_exchange -c "
  SELECT 
    COUNT(*) as user_count,
    (SELECT COUNT(*) FROM orders) as order_count,
    (SELECT COUNT(*) FROM transactions) as tx_count;
"

# 5. Check critical records exist
docker compose exec postgres psql -U cex -d cex_exchange -c "
  SELECT * FROM users LIMIT 5;
"
```

### Recovery Time Testing

Measure actual recovery time:

```bash
#!/bin/bash
# test_recovery_time.sh

START=$(date +%s)

echo "Starting recovery test..."

# Download backup
aws s3 cp s3://animica-cex-prod-backups/postgres/daily/latest.sql.gz ./

# Restore
./scripts/restore_postgres.sh latest.sql.gz

# Verify
docker compose exec postgres psql -U cex -d cex_exchange -c "SELECT COUNT(*) FROM users;"

END=$(date +%s)
DURATION=$((END - START))

echo "Recovery completed in $DURATION seconds"
echo "Target RTO: 7200 seconds (2 hours)"

if [ $DURATION -lt 7200 ]; then
    echo "✓ RTO target met"
else
    echo "✗ RTO target exceeded"
fi
```

### Backup Integrity Monitoring

```bash
# Check backup age
aws s3 ls s3://animica-cex-prod-backups/postgres/daily/ | tail -1

# Alert if backup older than 25 hours (expected: daily)
LATEST=$(aws s3 ls s3://animica-cex-prod-backups/postgres/daily/ --recursive | sort | tail -1 | awk '{print $1" "$2}')
LATEST_EPOCH=$(date -d "$LATEST" +%s)
NOW=$(date +%s)
AGE=$((NOW - LATEST_EPOCH))

if [ $AGE -gt 90000 ]; then  # 25 hours
    echo "⚠️  Backup is stale! Age: $((AGE / 3600)) hours"
fi
```

---

## Backup Monitoring

### Prometheus Alerts

```yaml
# Already configured in ops/monitoring/alerts/cex-alerts.yml
- alert: BackupFailed
  expr: time() - backup_last_success_timestamp > 90000  # 25 hours
  for: 1h
  labels:
    severity: critical
  annotations:
    summary: "Database backup has not succeeded in over 24 hours"
```

### Manual Checks

```bash
# Check backup job status
kubectl get jobs -n cex-prod | grep postgres-backup

# Check backup pod logs
kubectl logs -l job-name=postgres-backup-latest -n cex-prod

# Verify S3 uploads
aws s3 ls s3://animica-cex-prod-backups/postgres/daily/ --recursive | tail -10
```

---

## Best Practices

1. **Test Backups Regularly**
   - Monthly restore tests to dev environment
   - Quarterly DR drills

2. **Monitor Backup Health**
   - Alert on backup failures
   - Alert on stale backups
   - Monitor backup size trends

3. **Secure Backups**
   - Encrypt backups at rest (S3 encryption)
   - Encrypt backups in transit (TLS)
   - Restrict S3 bucket access
   - Enable S3 versioning
   - Enable S3 MFA delete

4. **Document Everything**
   - Keep runbook updated
   - Document restore procedures
   - Record RTO/RPO in each test

5. **Automate Recovery**
   - Script restore procedures
   - Test automation regularly
   - Minimize manual steps

---

## Troubleshooting

### Backup Fails

**Symptoms:** Backup CronJob fails

**Common Causes:**
- Postgres not accessible
- S3 credentials invalid
- Insufficient disk space
- Permission issues

**Resolution:**
```bash
# Check pod logs
kubectl logs -l job-name=postgres-backup-latest -n cex-prod

# Test postgres connection
kubectl exec -it postgres-0 -n cex-prod -- pg_isready

# Test S3 access
kubectl exec -it postgres-0 -n cex-prod -- \
  aws s3 ls s3://animica-cex-prod-backups/
```

### Restore Fails

**Symptoms:** Database restore errors

**Common Causes:**
- Corrupted backup file
- Wrong PostgreSQL version
- Insufficient disk space
- Connection errors

**Resolution:**
```bash
# Verify backup integrity
sha256sum -c backup.sql.gz.sha256

# Check PostgreSQL version
kubectl exec -it postgres-0 -n cex-prod -- psql --version

# Check disk space
kubectl exec -it postgres-0 -n cex-prod -- df -h
```

---

**Document Version**: 1.0  
**Last Updated**: 2024-01-25  
**Maintained By**: CEX Infrastructure Team
