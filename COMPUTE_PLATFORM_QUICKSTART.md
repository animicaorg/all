# Animica Compute Platform - Quick Start Guide

Complete setup guide for the Animica Compute + LLM Cloud Platform.

## Prerequisites

- **Docker** 20.10+ and **Docker Compose** v2.0+
- **Python** 3.11+
- **Node.js** 20+ (for web app)
- **pnpm** 9.0.0+ (Node package manager)
- 8GB+ RAM recommended
- 20GB+ disk space

## Quick Start (5 Minutes)

### 1. Clone and Setup

```bash
# Clone repository
git clone https://github.com/animicaorg/all.git
cd all

# Copy environment file
cp .env.compute.example .env

# Optional: Edit .env with your API keys
nano .env
```

### 2. Start All Services

```bash
# Start the complete platform
make compute-dev

# Or using docker-compose directly
docker-compose -f docker-compose.compute.yml up -d
```

This starts:
- PostgreSQL (database)
- Redis (cache)
- RabbitMQ (message queue)
- MinIO (object storage)
- API Gateway (port 8000)
- Auth Service (port 8001)
- Billing Service (port 8002)
- Inference Service (port 8003)
- Sandbox Runner (port 8004)
- GitHub App (port 8005)
- Model Registry (port 8006)
- Web App (port 3000)

### 3. Verify Services

```bash
# Check health of all services
curl http://localhost:8000/health  # API Gateway
curl http://localhost:8001/health  # Auth Service
curl http://localhost:8002/health  # Billing Service
curl http://localhost:3000         # Web App
```

### 4. Access the Platform

- **Web App**: http://localhost:3000
- **API Docs**: http://localhost:8000/docs
- **MinIO Console**: http://localhost:9001 (user: animica, pass: animica_dev_password)
- **RabbitMQ Management**: http://localhost:15672 (user: animica, pass: animica_dev_password)

## Service Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Web App (React)                          │
│                      http://localhost:3000                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      API Gateway (FastAPI)                       │
│                      http://localhost:8000                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Routing │ Auth Middleware │ Rate Limiting │ Logging      │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────┬───────┬──────────┬────────────┬──────────┬───────────────┘
      │       │          │            │          │
      ▼       ▼          ▼            ▼          ▼
  ┌─────┐ ┌──────┐  ┌────────┐  ┌────────┐  ┌──────────┐
  │Auth │ │Bill  │  │Infer   │  │Sandbox │  │GitHub    │
  │:8001│ │:8002 │  │:8003   │  │:8004   │  │App:8005  │
  └─────┘ └──────┘  └────────┘  └────────┘  └──────────┘
      │       │          │            │          │
      └───────┴──────────┴────────────┴──────────┘
                         │
              ┌──────────┴──────────┐
              │                     │
         ┌────▼────┐          ┌────▼────┐
         │Postgres │          │ Redis   │
         │  :5432  │          │  :6379  │
         └─────────┘          └─────────┘
```

## Development Workflow

### Running Individual Services

Each service can be run independently for development:

```bash
# Auth Service
cd packages/auth-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn auth_service.main:app --reload --port 8001

# Billing Service
cd packages/billing-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn billing_service.main:app --reload --port 8002

# Web App
cd packages/web
pnpm install
pnpm dev
```

### Database Migrations

```bash
# Create migration
cd packages/auth-service
alembic revision --autogenerate -m "Add new table"

# Run migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

### Testing

```bash
# Run all tests
make compute-test

# Test specific service
cd packages/auth-service
pytest tests/ -v

# With coverage
pytest tests/ --cov=auth_service --cov-report=html
```

### Linting and Formatting

```bash
# Lint and format all services
make compute-lint

# Or per service
cd packages/auth-service
black auth_service/
ruff check auth_service/ --fix
```

## Common Tasks

### Register a New User

```bash
curl -X POST http://localhost:8001/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "securepassword123"
  }'
```

### Login and Get Token

```bash
curl -X POST http://localhost:8001/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "securepassword123"
  }'
```

### Check Credit Balance

```bash
curl -X GET http://localhost:8002/billing/balance \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

### Chat Completion (OpenAI-compatible)

```bash
curl -X POST http://localhost:8003/v1/chat/completions \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama-3-8b-instruct",
    "messages": [
      {"role": "user", "content": "Hello, how are you?"}
    ],
    "stream": false
  }'
```

### Execute Code in Sandbox

```bash
curl -X POST http://localhost:8004/execute \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "language": "python",
    "code": "print(\"Hello from sandbox!\")"
  }'
```

## Environment Variables Reference

See `.env.compute.example` for all available configuration options.

### Critical Settings

- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `JWT_SECRET_KEY`: **Change in production!**
- `STRIPE_API_KEY`: Stripe API key for payments
- `ANIMICA_RPC_URL`: Animica blockchain RPC endpoint

### Optional Features

- `FEATURE_CHAT_DASHBOARD=true`: Enable chat UI
- `FEATURE_CODE_WORKSPACE=true`: Enable code editor
- `FEATURE_GITHUB_APP=true`: Enable GitHub integration
- `FEATURE_GPU_CONTRIBUTOR=false`: Enable GPU contributor node

## Monitoring and Debugging

### View Logs

```bash
# All services
make compute-logs

# Specific service
docker-compose -f docker-compose.compute.yml logs -f auth-service
```

### Database Access

```bash
# PostgreSQL
docker exec -it animica-compute-network-postgres-1 \
  psql -U animica -d animica_compute

# Redis
docker exec -it animica-compute-network-redis-1 redis-cli
```

### Metrics and Monitoring

- Prometheus metrics exposed at `http://localhost:9090`
- Health checks at `http://localhost:800X/health` for each service

## Troubleshooting

### Services Won't Start

```bash
# Clean everything and restart
make compute-clean
make compute-dev
```

### Database Connection Issues

```bash
# Check Postgres is running
docker-compose -f docker-compose.compute.yml ps postgres

# View postgres logs
docker-compose -f docker-compose.compute.yml logs postgres
```

### Port Conflicts

If ports 8000-8006 or 3000 are in use, update `docker-compose.compute.yml` to use different ports.

## Production Deployment

For production deployment:

1. **Use proper secrets**: Generate secure values for `JWT_SECRET_KEY`, database passwords
2. **Enable HTTPS**: Set `HTTPS_ENABLED=true` and provide SSL certificates
3. **Use managed databases**: PostgreSQL RDS, Redis ElastiCache, etc.
4. **Configure monitoring**: Set up Grafana, Prometheus, and alerts
5. **Enable backups**: Database backups, disaster recovery
6. **Use Kubernetes**: See `ops/k8s/` for manifests
7. **Set resource limits**: CPU, memory limits in production

See `infra/` directory for Terraform configurations.

## Next Steps

1. **Read the docs**: `docs/compute-platform/` for detailed guides
2. **Explore the API**: http://localhost:8000/docs
3. **Try the web app**: http://localhost:3000
4. **Join Discord**: https://discord.gg/animica (community support)
5. **Contribute**: See CONTRIBUTING.md

## Support

- **Documentation**: `docs/compute-platform/`
- **GitHub Issues**: https://github.com/animicaorg/all/issues
- **Discord**: https://discord.gg/animica
- **Email**: support@animica.ai

## License

See LICENSE.txt in repository root.
