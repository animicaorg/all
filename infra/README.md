# Animica Compute Platform - Infrastructure

Infrastructure as Code for deploying the Animica Compute Platform.

## Structure

```
infra/
├── terraform/          # Terraform modules for cloud resources
│   ├── aws/           # AWS deployment
│   ├── gcp/           # Google Cloud deployment
│   ├── azure/         # Azure deployment
│   └── modules/       # Reusable modules
├── kubernetes/        # Kubernetes manifests
│   ├── base/         # Base configurations
│   ├── overlays/     # Environment-specific overlays
│   └── operators/    # Custom operators
└── helm/             # Helm charts
    └── animica-compute/  # Main chart
```

## Deployment Options

### 1. Local Development (Docker Compose)

```bash
cd /home/runner/work/all/all
make compute-dev
```

### 2. Kubernetes (Minikube/Kind)

```bash
# Start local cluster
minikube start --cpus=4 --memory=8192

# Deploy
kubectl apply -k infra/kubernetes/overlays/development
```

### 3. Cloud Deployment (AWS)

```bash
cd infra/terraform/aws
terraform init
terraform plan
terraform apply
```

## Prerequisites

### Tools
- Terraform >= 1.5
- kubectl >= 1.27
- Helm >= 3.12
- Docker >= 24.0

### Cloud Provider Credentials
- AWS: `~/.aws/credentials`
- GCP: `gcloud auth application-default login`
- Azure: `az login`

## Terraform Modules

### AWS Module

Provisions:
- EKS cluster with GPU node groups
- RDS PostgreSQL database
- ElastiCache Redis cluster
- S3 bucket for model storage
- VPC with private/public subnets
- Application Load Balancer
- CloudWatch for logging

Usage:
```hcl
module "animica_compute" {
  source = "../../modules/compute-platform"
  
  cluster_name = "animica-prod"
  region = "us-east-1"
  
  gpu_node_instance_type = "g5.xlarge"
  gpu_node_count = 3
  
  database_instance_class = "db.t3.large"
  redis_node_type = "cache.r6g.large"
}
```

## Kubernetes Configuration

### Namespaces

- `animica-compute` - Main application services
- `animica-monitoring` - Prometheus, Grafana, Jaeger
- `animica-system` - Operators, CRDs

### Services

Each microservice has:
- Deployment with resource limits
- HorizontalPodAutoscaler
- Service (ClusterIP/LoadBalancer)
- ConfigMap for configuration
- Secret for sensitive data
- NetworkPolicy for security

Example:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-gateway
spec:
  replicas: 3
  selector:
    matchLabels:
      app: api-gateway
  template:
    metadata:
      labels:
        app: api-gateway
    spec:
      containers:
      - name: api-gateway
        image: animica/api-gateway:latest
        resources:
          requests:
            cpu: "500m"
            memory: "512Mi"
          limits:
            cpu: "2000m"
            memory: "2Gi"
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: database-credentials
              key: url
```

## Helm Charts

Main chart: `animica-compute`

Install:
```bash
helm repo add animica https://charts.animica.ai
helm install animica-compute animica/animica-compute \
  --namespace animica-compute \
  --create-namespace \
  --values values-production.yaml
```

Configuration options in `values.yaml`:
- Replica counts
- Resource limits
- External service endpoints
- Feature flags
- Observability settings

## Monitoring & Observability

### Prometheus

Metrics collected:
- HTTP request rate, latency, errors
- Database connection pool stats
- GPU utilization
- Queue depth
- Credit ledger balance

### Grafana Dashboards

- API Gateway Overview
- Inference Service Performance
- Billing Metrics
- Marketplace Activity

### Logging (ELK Stack)

- Structured JSON logs
- Log aggregation
- Full-text search
- Retention: 30 days

### Tracing (Jaeger)

- Distributed traces across services
- Request flow visualization
- Performance bottleneck identification

## Security

### Network Policies

- Deny all by default
- Explicit allow rules for service communication
- External traffic only through ingress

### Secrets Management

Options:
1. Kubernetes Secrets (base64 encoded)
2. HashiCorp Vault (recommended)
3. AWS Secrets Manager
4. Azure Key Vault

### TLS/HTTPS

- Cert-manager for automatic certificate provisioning
- Let's Encrypt integration
- TLS 1.3 minimum

## Backup & Disaster Recovery

### Database Backups

- Automated daily backups
- Point-in-time recovery (PITR)
- Cross-region replication
- 30-day retention

### Disaster Recovery Plan

- RTO: 4 hours
- RPO: 1 hour
- Multi-region failover
- Regular DR drills

## Scaling

### Horizontal Scaling

Auto-scaling based on:
- CPU utilization (> 70%)
- Memory utilization (> 80%)
- Queue depth (> 100 items)
- Request rate (> 1000 req/s)

### Vertical Scaling

GPU nodes:
- Start: g5.xlarge (1x A10G, 24GB VRAM)
- Scale: g5.12xlarge (4x A10G, 96GB VRAM)
- Large models: p4d.24xlarge (8x A100, 320GB VRAM)

## Cost Optimization

- Spot instances for non-critical workloads
- Reserved instances for predictable load
- Auto-shutdown during low usage
- S3 lifecycle policies for model versioning

## Troubleshooting

### Common Issues

1. **Pod CrashLoopBackOff**
   ```bash
   kubectl logs -n animica-compute <pod-name>
   kubectl describe pod -n animica-compute <pod-name>
   ```

2. **Service unreachable**
   ```bash
   kubectl get svc -n animica-compute
   kubectl get endpoints -n animica-compute
   ```

3. **GPU not detected**
   ```bash
   kubectl describe node <node-name> | grep -A 10 "Allocated resources"
   ```

## CI/CD

### GitHub Actions Workflow

1. Build Docker images
2. Run tests
3. Vulnerability scanning (Trivy)
4. Push to container registry
5. Update Kubernetes manifests
6. ArgoCD auto-sync

### Deployment Strategy

- Development: Continuous deployment
- Staging: Daily deployment
- Production: Blue-green deployment with manual approval

## References

- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [Kubernetes Documentation](https://kubernetes.io/docs/home/)
- [Helm Documentation](https://helm.sh/docs/)
- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)
