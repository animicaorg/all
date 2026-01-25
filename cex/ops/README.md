# CEX Operations & Infrastructure

Complete infrastructure and deployment setup for the Animica Centralized Exchange (CEX).

## Quick Links

- 📚 [Deployment Guide](../../docs/deployment.md) - How to deploy to dev, staging, and production
- 📖 [Operational Runbook](../../docs/runbook.md) - Day-to-day operations and incident response
- 💾 [Backup & Recovery Guide](../../docs/backups.md) - Backup strategies and disaster recovery
- 📊 [Monitoring Guide](../../docs/monitoring.md) - Metrics, alerts, and dashboards

## Overview

This directory contains all infrastructure-as-code for the CEX platform:

```
ops/
├── docker/               # Docker Compose configurations
│   ├── docker-compose.dev.yml       # Development environment
│   ├── docker-compose.prod.yml      # Production-like environment
│   ├── docker-compose.monitoring.yml # Monitoring stack
│   ├── env/              # Environment configuration examples
│   └── scripts/          # Automation scripts
├── k8s/                  # Kubernetes manifests
│   ├── base/             # Base manifests (infrastructure + services)
│   └── overlays/         # Environment-specific overlays
├── nginx/                # Nginx reverse proxy configurations
└── monitoring/           # Monitoring configurations (Prometheus, Grafana, etc.)
```

## 🚀 Quick Start

### Development Environment

Start the complete development stack with one command:

```bash
cd ops/docker
./scripts/up_dev.sh
```

This will:
- ✅ Start infrastructure (Postgres, Redis, NATS, MinIO, MailHog)
- ✅ Run database migrations
- ✅ Start all application services
- ✅ Run health checks

**With monitoring:**
```bash
./scripts/up_dev.sh --with-monitoring
```

### Access URLs (Development)

- **API Gateway**: http://localhost:3000
- **Admin Service**: http://localhost:3001
- **PostgreSQL**: localhost:5432
- **Redis**: localhost:6379
- **NATS**: localhost:4222, Monitor: http://localhost:8222
- **MailHog**: http://localhost:8025
- **MinIO**: http://localhost:9001
- **Grafana** (with --with-monitoring): http://localhost:3100
- **Prometheus** (with --with-monitoring): http://localhost:9090

### Production Deployment (Kubernetes)

```bash
# 1. Create secrets
kubectl create secret generic cex-secrets \
  --from-literal=db-password='...' \
  --namespace=cex-prod

# 2. Deploy infrastructure
kubectl apply -f k8s/base/postgres/
kubectl apply -f k8s/base/redis/
kubectl apply -f k8s/base/nats/

# 3. Run migrations
# (See deployment guide)

# 4. Deploy services
kubectl apply -k k8s/overlays/prod/

# 5. Deploy ingress
kubectl apply -f k8s/base/ingress/
```

See [Deployment Guide](../../docs/deployment.md) for detailed instructions.

## 📦 Components

### Docker Compose Files

**docker-compose.dev.yml**
- Complete development environment
- All services with hot reload
- Development dependencies (MailHog, MinIO)
- Bind mounts for live code updates

**docker-compose.prod.yml**
- Production-like configuration
- Built images (no bind mounts)
- Resource limits
- Nginx reverse proxy
- Secrets management

**docker-compose.monitoring.yml**
- Prometheus (metrics collection)
- Grafana (visualization)
- Loki + Promtail (log aggregation)
- AlertManager (alerting)
- Exporters (postgres, redis, node)

### Automation Scripts

Located in `docker/scripts/`:

- **up_dev.sh** - Start complete dev environment
- **down_dev.sh** - Stop all services
- **logs.sh** - View service logs
- **migrate.sh** - Run database migrations
- **backup_postgres.sh** - Create database backup
- **restore_postgres.sh** - Restore from backup
- **seed_admin.sh** - Create admin user
- **smoke_test.sh** - Validate service health
- **wait_for.sh** - Wait for service availability

All scripts are executable and include help text.

### Kubernetes Manifests

**Base Manifests** (`k8s/base/`):
- **Infrastructure**: Postgres StatefulSet, Redis, NATS
- **Services**: API Gateway, Matching Engine, Ledger, etc.
- **Networking**: Ingress, NetworkPolicies
- **Security**: RBAC, PodSecurityPolicies
- **Scaling**: HorizontalPodAutoscalers
- **Backups**: CronJob for automated backups

**Overlays** (`k8s/overlays/`):
- **dev/**: Development configuration
- **prod/**: Production configuration

Use with kustomize:
```bash
kubectl apply -k k8s/overlays/prod/
```

### Monitoring Configuration

**Prometheus** (`monitoring/prometheus.yml`):
- Service discovery
- Scrape configurations
- Recording rules

**Alert Rules** (`monitoring/alerts/cex-alerts.yml`):
- Service health alerts
- Performance alerts
- Business metric alerts
- Infrastructure alerts

**Grafana Dashboards** (`monitoring/dashboards/`):
- Exchange overview dashboard
- Infrastructure dashboard
- Business metrics dashboard

**Loki** (`monitoring/loki-config.yml`):
- Log aggregation configuration
- Retention policies

## 🏗️ Architecture

### Service Architecture

```
┌─────────────┐
│   Ingress   │ (Nginx / K8s Ingress)
└──────┬──────┘
       │
┌──────▼──────────────────────────────┐
│       API Gateway (Public)          │
│       Admin Service (Restricted)    │
└──────┬──────────────────────────────┘
       │
┌──────┴──────┬─────────┬──────────┬──────────┐
│             │         │          │          │
▼             ▼         ▼          ▼          ▼
Auth      Matching  Ledger  Withdrawals  Animica
Service   Engine    Service  Service      Scanner
│             │         │          │          │
└─────────────┴─────────┴──────────┴──────────┘
                      │
        ┌─────────────┴─────────────┐
        │                           │
   ┌────▼────┐  ┌──────▼──────┐  ┌─▼────┐
   │Postgres │  │   Redis     │  │ NATS │
   └─────────┘  └─────────────┘  └──────┘
```

### Data Flow

1. **Deposits**: Animica Scanner → NATS → Wallet Router → Ledger
2. **Trading**: API Gateway → Matching Engine → Ledger → NATS
3. **Withdrawals**: Admin/API → Risk Service → Wallet Router → BitGo

## 🔒 Security

### Secrets Management

**Development:**
- Environment variables in `.env` files
- Example files in `docker/env/*.example`
- Never commit real secrets

**Production:**
- Kubernetes Secrets
- External secrets management recommended (Vault, AWS Secrets Manager)
- Secret rotation procedures in runbook

### Network Security

**Docker:**
- Internal network for services
- Edge network for public services
- No exposed database ports

**Kubernetes:**
- NetworkPolicies (default deny)
- Pod Security Policies
- RBAC for service accounts
- TLS for ingress

### Container Security

All Dockerfiles:
- Multi-stage builds
- Run as non-root user
- Minimal base images
- Health checks
- No secrets in images

## 📊 Monitoring & Alerts

### Key Metrics

**Golden Signals:**
- Latency: Request duration (p50, p95, p99)
- Traffic: Requests per second
- Errors: Error rate (%)
- Saturation: Resource usage (CPU, memory, disk)

**Business Metrics:**
- Orders created/matched/cancelled
- Trade volume
- Deposits/Withdrawals processed
- Active users

**Infrastructure:**
- Database connections, query duration
- Redis memory usage
- NATS queue size, message rate
- Scanner block lag

### Alert Levels

- **Critical (P0)**: Immediate response - Service down, data loss risk
- **Warning (P1)**: Requires attention - Performance degradation, high resource usage
- **Info**: Informational - Deployments, scaling events

See [Monitoring Guide](../../docs/monitoring.md) for full details.

## 🔄 CI/CD

### Build Pipeline

```yaml
Build → Test → Security Scan → Push Image → Deploy to Dev → E2E Tests → Deploy to Prod
```

### Deployment Strategy

**Development:**
- Continuous deployment from main branch
- Auto-deploy on merge

**Staging:**
- Manual trigger from main branch
- Smoke tests required

**Production:**
- Manual approval required
- Rolling update strategy
- Canary deployment option
- Automatic rollback on failure

## 📝 Operations

### Daily Tasks

- Monitor dashboards for anomalies
- Review alerts
- Check backup completion
- Review logs for errors

### Weekly Tasks

- Test backup restore
- Review capacity metrics
- Update documentation
- Security patches

### Monthly Tasks

- DR drill
- Performance review
- Cost optimization
- Dependency updates

## 🆘 Incident Response

### P0: Service Down

1. Check service health in Grafana
2. View recent logs
3. Restart service if necessary
4. Escalate if not resolved in 15 minutes

### P1: Performance Degradation

1. Check resource usage
2. Identify bottleneck
3. Scale if needed
4. Investigate root cause

### P2: Resource Warning

1. Review trends
2. Plan capacity increase
3. Optimize if possible
4. Schedule maintenance

See [Runbook](../../docs/runbook.md) for detailed procedures.

## 🧪 Testing

### Smoke Tests

Run after deployment:
```bash
./scripts/smoke_test.sh
```

Validates:
- ✅ All services healthy
- ✅ Endpoints responding
- ✅ Database accessible
- ✅ Infrastructure ready

### Load Testing

Use provided load test scripts:
```bash
# (Location TBD based on your setup)
./tests/load/run_load_test.sh
```

### DR Drill

Quarterly disaster recovery test:
```bash
# Follow DR procedure in backup guide
# Document RTO/RPO
# Update procedures based on results
```

## 📞 Support

### Documentation

- [Architecture Docs](../../docs/architecture.md)
- [Security Baseline](../../docs/security_baseline.md)
- [API Documentation](../../docs/api/)

### Getting Help

1. Check documentation
2. Review runbook
3. Search logs in Grafana
4. Check #cex-ops Slack channel
5. Page on-call engineer (P0/P1 only)

### Contributing

1. Update this README for infrastructure changes
2. Keep runbook current with actual procedures
3. Document all operational changes
4. Test scripts before committing

## 📜 License

Proprietary - Animica Foundation

---

**Last Updated**: 2024-01-25  
**Maintained By**: CEX Infrastructure Team  
**Version**: 1.0
