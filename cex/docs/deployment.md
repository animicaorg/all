# CEX Deployment Guide

## Table of Contents
1. [Overview](#overview)
2. [Environment Setup](#environment-setup)
3. [Docker Compose Deployment](#docker-compose-deployment)
4. [Kubernetes Deployment](#kubernetes-deployment)
5. [CI/CD Integration](#cicd-integration)
6. [Blue-Green Deployment](#blue-green-deployment)
7. [Rollback Procedures](#rollback-procedures)

---

## Overview

This guide covers deployment procedures for the CEX (Centralized Exchange) platform across different environments:

- **Development**: Local Docker Compose
- **Staging**: Kubernetes cluster (optional)
- **Production**: Kubernetes cluster with HA configuration

### Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Load Balancer / Ingress              │
└────────────────────┬────────────────────────────────────┘
                     │
     ┌───────────────┴───────────────┐
     │                               │
┌────▼─────┐                  ┌─────▼────┐
│API       │                  │Admin     │
│Gateway   │◄────────────────►│Service   │
└────┬─────┘                  └─────┬────┘
     │                               │
     ├───────────────┬───────────────┤
     │               │               │
┌────▼─────┐  ┌─────▼────┐  ┌──────▼──────┐
│Matching  │  │Ledger    │  │Withdrawals  │
│Engine    │  │Service   │  │Service      │
└────┬─────┘  └─────┬────┘  └──────┬──────┘
     │               │               │
     └───────────────┼───────────────┘
                     │
     ┌───────────────┴───────────────┐
     │                               │
┌────▼─────┐  ┌─────▼────┐  ┌──────▼──────┐
│Postgres  │  │Redis     │  │NATS         │
│          │  │          │  │             │
└──────────┘  └──────────┘  └─────────────┘
```

---

## Environment Setup

### Prerequisites

**All Environments:**
- Git
- Docker 20+
- Docker Compose v2

**Kubernetes Deployments:**
- kubectl
- kustomize
- Access to Kubernetes cluster
- Access to container registry

### Environment Variables

Create environment files based on templates:
```bash
# Development
cp cex/ops/docker/env/dev.env.example cex/ops/docker/env/.env

# Production (Kubernetes ConfigMap/Secrets)
# See K8s deployment section
```

---

## Docker Compose Deployment

### Development Environment

#### Quick Start
```bash
cd cex/ops/docker
./scripts/up_dev.sh
```

This performs:
1. Environment validation
2. Infrastructure startup (Postgres, Redis, NATS)
3. Database migrations
4. Service startup
5. Health checks

#### With Monitoring Stack
```bash
./scripts/up_dev.sh --with-monitoring
```

Includes:
- Prometheus (metrics)
- Grafana (dashboards)
- Loki (logs)
- AlertManager (alerts)

#### Manual Step-by-Step

1. **Start Infrastructure**
```bash
docker compose -f docker-compose.dev.yml up -d postgres redis nats
```

2. **Wait for Health**
```bash
./scripts/wait_for.sh postgres 5432 60
./scripts/wait_for.sh redis 6379 30
./scripts/wait_for.sh nats 4222 30
```

3. **Run Migrations**
```bash
docker compose -f docker-compose.dev.yml run --rm migrate
```

4. **Start Services**
```bash
docker compose -f docker-compose.dev.yml up -d
```

5. **Verify**
```bash
./scripts/smoke_test.sh
```

### Production-Like Environment (Docker Compose)

For testing production configurations locally:

```bash
cd cex/ops/docker

# Build images
docker compose -f docker-compose.prod.yml build

# Start services
docker compose -f docker-compose.prod.yml up -d

# Check health
docker compose -f docker-compose.prod.yml ps
```

**Note:** This uses the production docker-compose file with:
- Built images (no bind mounts)
- Resource limits
- Nginx reverse proxy
- Secrets management

### Stopping Services

```bash
cd cex/ops/docker

# Stop all services
./scripts/down_dev.sh

# Or manually
docker compose -f docker-compose.dev.yml down

# With volume cleanup (⚠️ destroys data)
docker compose -f docker-compose.dev.yml down -v
```

---

## Kubernetes Deployment

### Prerequisites

1. **Cluster Access**
```bash
# Verify connection
kubectl cluster-info
kubectl get nodes
```

2. **Container Registry**
```bash
# Login to registry
docker login registry.animica.io

# Or create imagePullSecret
kubectl create secret docker-registry registry-credentials \
  --docker-server=registry.animica.io \
  --docker-username=$REGISTRY_USER \
  --docker-password=$REGISTRY_PASSWORD \
  --namespace=cex-prod
```

3. **Storage Class**
```bash
# Verify storage class exists
kubectl get storageclass
```

### Initial Production Deployment

#### Step 1: Create Namespace and Secrets

```bash
# Create namespaces
kubectl apply -f ops/k8s/base/namespace.yaml

# Create secrets
kubectl create secret generic cex-secrets \
  --from-literal=db-user='cex_prod' \
  --from-literal=db-password='SECURE_DB_PASSWORD' \
  --from-literal=redis-password='SECURE_REDIS_PASSWORD' \
  --from-literal=jwt-secret='SECURE_JWT_SECRET' \
  --from-literal=session-secret='SECURE_SESSION_SECRET' \
  --from-literal=bitgo-access-token='BITGO_TOKEN' \
  --from-literal=bitgo-webhook-secret='WEBHOOK_SECRET' \
  --from-literal=admin-api-key='ADMIN_API_KEY' \
  --from-literal=smtp-user='SMTP_USER' \
  --from-literal=smtp-password='SMTP_PASSWORD' \
  --from-literal=s3-access-key='S3_ACCESS_KEY' \
  --from-literal=s3-secret-key='S3_SECRET_KEY' \
  --namespace=cex-prod

# Verify secrets
kubectl get secrets -n cex-prod
```

**Security Note:** In production, use external secrets management (AWS Secrets Manager, HashiCorp Vault, etc.) instead of kubectl create secret.

#### Step 2: Deploy ConfigMaps

```bash
kubectl apply -f ops/k8s/base/configmaps.yaml
```

#### Step 3: Deploy Infrastructure

```bash
# PostgreSQL
kubectl apply -f ops/k8s/base/postgres/statefulset.yaml
kubectl apply -f ops/k8s/base/postgres/service.yaml
kubectl apply -f ops/k8s/base/postgres/pdb.yaml

# Wait for Postgres to be ready
kubectl wait --for=condition=ready pod -l app=postgres -n cex-prod --timeout=300s

# Redis
kubectl apply -f ops/k8s/base/redis/deployment.yaml
kubectl apply -f ops/k8s/base/redis/service.yaml

# NATS
kubectl apply -f ops/k8s/base/nats/statefulset.yaml
kubectl apply -f ops/k8s/base/nats/service.yaml

# Wait for all infrastructure
kubectl wait --for=condition=ready pod -l app=redis -n cex-prod --timeout=120s
kubectl wait --for=condition=ready pod -l app=nats -n cex-prod --timeout=120s
```

#### Step 4: Run Database Migrations

```bash
# Create migration job
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: db-migrate-$(date +%Y%m%d-%H%M%S)
  namespace: cex-prod
spec:
  backoffLimit: 3
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: migrate
        image: registry.animica.io/cex-migrations:latest
        command: ["sh", "-c", "pnpm migrate"]
        envFrom:
        - configMapRef:
            name: cex-config
        env:
        - name: DB_USER
          valueFrom:
            secretKeyRef:
              name: cex-secrets
              key: db-user
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: cex-secrets
              key: db-password
EOF

# Monitor migration
kubectl logs -f job/db-migrate-TIMESTAMP -n cex-prod

# Verify success
kubectl get jobs -n cex-prod
```

#### Step 5: Deploy Application Services

**Option A: Using Kustomize (Recommended)**
```bash
# Deploy production overlay
kubectl apply -k ops/k8s/overlays/prod/

# Verify deployment
kubectl get deployments -n cex-prod
kubectl get pods -n cex-prod
kubectl get services -n cex-prod
```

**Option B: Manual Deployment**
```bash
# Deploy each service
kubectl apply -f ops/k8s/base/services/api-gateway.yaml
# ... repeat for other services

# Verify
kubectl get deployments -n cex-prod
```

#### Step 6: Deploy Network Policies

```bash
kubectl apply -f ops/k8s/base/networkpolicies/default-deny.yaml
kubectl apply -f ops/k8s/base/networkpolicies/allow-internal.yaml

# Verify
kubectl get networkpolicies -n cex-prod
```

#### Step 7: Deploy Ingress

```bash
# Ensure cert-manager is installed in cluster
kubectl get pods -n cert-manager

# Deploy ingress
kubectl apply -f ops/k8s/base/ingress/ingress.yaml
kubectl apply -f ops/k8s/base/ingress/certificates.yaml

# Wait for certificate issuance
kubectl get certificate -n cex-prod
kubectl describe certificate api-tls -n cex-prod

# Verify ingress
kubectl get ingress -n cex-prod
```

#### Step 8: Setup Monitoring

```bash
# Deploy monitoring stack (if using in-cluster Prometheus)
kubectl apply -f ops/k8s/base/monitoring/

# Or use external monitoring solution
```

#### Step 9: Configure Backups

```bash
# Deploy backup CronJob
kubectl apply -f ops/k8s/base/postgres/backup-cronjob.yaml

# Verify
kubectl get cronjob -n cex-prod

# Test backup manually
kubectl create job --from=cronjob/postgres-backup test-backup -n cex-prod
kubectl logs -f job/test-backup -n cex-prod
```

#### Step 10: Smoke Tests

```bash
# Check pod health
kubectl get pods -n cex-prod

# Test API endpoint
curl -f https://api.animica.io/health

# Test admin endpoint
curl -f https://admin.animica.io/health

# Check metrics
curl https://api.animica.io/metrics | head -20
```

### Updating Existing Deployment

#### Update Application Image

```bash
# Build new image
docker build -t registry.animica.io/cex-api-gateway:v1.2.3 -f services/api-gateway/Dockerfile .

# Push to registry
docker push registry.animica.io/cex-api-gateway:v1.2.3

# Update deployment
kubectl set image deployment/api-gateway \
  api-gateway=registry.animica.io/cex-api-gateway:v1.2.3 \
  -n cex-prod \
  --record

# Monitor rollout
kubectl rollout status deployment/api-gateway -n cex-prod

# Verify
kubectl get pods -l app=api-gateway -n cex-prod
```

#### Update ConfigMap

```bash
# Edit configmap
kubectl edit configmap cex-config -n cex-prod

# Or apply updated file
kubectl apply -f ops/k8s/base/configmaps.yaml

# Restart deployments to pick up changes
kubectl rollout restart deployment/api-gateway -n cex-prod
```

#### Update Secrets

```bash
# Update secret
kubectl create secret generic cex-secrets \
  --from-literal=jwt-secret='NEW_JWT_SECRET' \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart deployments
kubectl rollout restart deployment/api-gateway -n cex-prod
```

---

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Deploy to Production

on:
  push:
    branches: [main]
    paths:
      - 'cex/**'

env:
  REGISTRY: registry.animica.io
  IMAGE_NAME: cex-api-gateway

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Login to Registry
        uses: docker/login-action@v2
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ secrets.REGISTRY_USERNAME }}
          password: ${{ secrets.REGISTRY_PASSWORD }}
      
      - name: Build and Push
        uses: docker/build-push-action@v4
        with:
          context: ./cex
          file: ./cex/services/api-gateway/Dockerfile
          push: true
          tags: |
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }}
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
      
      - name: Configure kubectl
        uses: azure/k8s-set-context@v3
        with:
          method: kubeconfig
          kubeconfig: ${{ secrets.KUBE_CONFIG }}
      
      - name: Deploy to Kubernetes
        run: |
          kubectl set image deployment/api-gateway \
            api-gateway=${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }} \
            -n cex-prod
          kubectl rollout status deployment/api-gateway -n cex-prod
      
      - name: Verify Deployment
        run: |
          kubectl get pods -l app=api-gateway -n cex-prod
          curl -f https://api.animica.io/health
```

---

## Blue-Green Deployment

For zero-downtime deployments with instant rollback capability:

### Setup

1. **Create Blue Environment**
```bash
kubectl apply -k ops/k8s/overlays/prod/ -n cex-prod-blue
```

2. **Create Green Environment**
```bash
kubectl apply -k ops/k8s/overlays/prod/ -n cex-prod-green
```

3. **Update Ingress to Route to Blue**
```yaml
# Update ingress to point to blue
spec:
  rules:
  - host: api.animica.io
    http:
      paths:
      - path: /
        backend:
          service:
            name: api-gateway
            port:
              number: 3000
          namespace: cex-prod-blue  # Active
```

### Deployment Process

1. **Deploy to Green** (inactive)
```bash
kubectl set image deployment/api-gateway \
  api-gateway=registry.animica.io/cex-api-gateway:v1.2.3 \
  -n cex-prod-green
```

2. **Test Green**
```bash
# Port-forward to test
kubectl port-forward deployment/api-gateway 8080:3000 -n cex-prod-green

# Run tests against localhost:8080
curl http://localhost:8080/health
```

3. **Switch Traffic to Green**
```bash
# Update ingress
kubectl patch ingress cex-ingress -n cex-prod --type=json \
  -p='[{"op": "replace", "path": "/spec/rules/0/http/paths/0/backend/service/namespace", "value":"cex-prod-green"}]'
```

4. **Monitor**
```bash
# Watch metrics in Grafana
# Monitor error rates
# Check logs
```

5. **Rollback if Needed** (switch back to blue)
```bash
kubectl patch ingress cex-ingress -n cex-prod --type=json \
  -p='[{"op": "replace", "path": "/spec/rules/0/http/paths/0/backend/service/namespace", "value":"cex-prod-blue"}]'
```

---

## Rollback Procedures

### Rolling Update Rollback

```bash
# View rollout history
kubectl rollout history deployment/api-gateway -n cex-prod

# Rollback to previous version
kubectl rollout undo deployment/api-gateway -n cex-prod

# Rollback to specific revision
kubectl rollout undo deployment/api-gateway --to-revision=2 -n cex-prod

# Verify
kubectl rollout status deployment/api-gateway -n cex-prod
kubectl get pods -l app=api-gateway -n cex-prod
```

### Database Rollback

⚠️ **Complex operation - requires careful execution**

1. Stop all services
2. Restore database from backup (see [Backup Guide](./backups.md))
3. Rollback code to matching version
4. Restart services

### Full System Rollback

```bash
# 1. Scale down all services
kubectl scale deployment --all --replicas=0 -n cex-prod

# 2. Restore database
# (See backup guide)

# 3. Deploy previous version
kubectl apply -k ops/k8s/overlays/prod-previous/

# 4. Verify
kubectl get pods -n cex-prod
curl https://api.animica.io/health
```

---

## Troubleshooting Deployment Issues

### Pod Won't Start
```bash
# Check pod status
kubectl describe pod <pod-name> -n cex-prod

# Check logs
kubectl logs <pod-name> -n cex-prod

# Check events
kubectl get events -n cex-prod --sort-by='.lastTimestamp'
```

### Image Pull Errors
```bash
# Verify imagePullSecret
kubectl get secret registry-credentials -n cex-prod

# Test pull manually
docker pull registry.animica.io/cex-api-gateway:latest
```

### Service Not Accessible
```bash
# Check service
kubectl get svc -n cex-prod
kubectl describe svc api-gateway -n cex-prod

# Check endpoints
kubectl get endpoints api-gateway -n cex-prod

# Check ingress
kubectl get ingress -n cex-prod
kubectl describe ingress cex-ingress -n cex-prod
```

---

## Checklist

### Pre-Deployment
- [ ] Code reviewed and tested
- [ ] Database migrations tested
- [ ] Environment variables updated
- [ ] Secrets configured
- [ ] Monitoring configured
- [ ] Backup verified
- [ ] Rollback plan ready
- [ ] Team notified

### Post-Deployment
- [ ] All pods running
- [ ] Health checks passing
- [ ] Smoke tests passed
- [ ] Metrics normal
- [ ] No error spikes
- [ ] Backup scheduled
- [ ] Documentation updated
- [ ] Team notified

---

**Document Version**: 1.0  
**Last Updated**: 2024-01-25  
**Maintained By**: CEX Infrastructure Team
