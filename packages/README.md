# Animica Compute Platform - Packages

This directory contains all microservices and applications for the Animica Compute + LLM Cloud Platform.

## Package Structure

### Frontend Applications
- **web/**: Main web application (React/TypeScript)
  - Chat dashboard
  - Code editor workspace
  - Admin dashboards
  - User settings

### Backend Services
- **api/**: API Gateway (FastAPI)
  - Request routing
  - Authentication middleware
  - Rate limiting
  - Request/response transformation

- **auth-service/**: Authentication & Authorization (FastAPI)
  - User registration and login
  - Wallet signature verification
  - OAuth2/OIDC integration
  - RBAC enforcement

- **billing-service/**: Billing & Payments (FastAPI)
  - Stripe integration
  - PayPal integration
  - Credit ledger management
  - Usage tracking

- **inference/**: LLM Inference Service (Python/vLLM)
  - Model serving
  - Chat completions API
  - Token streaming
  - GPU management

- **sandbox-runner/**: Code Execution Sandbox (Python/gVisor)
  - Secure code execution
  - Multi-language support
  - Resource isolation

- **queue-service/**: Job Queue Service (Python/Celery)
  - Task distribution
  - Worker management
  - Job scheduling

- **github-app/**: GitHub Integration (FastAPI)
  - Webhook handler
  - PR automation
  - Issue management

- **model-registry/**: Model Management (FastAPI)
  - Model versioning
  - Health checks
  - Rollout policies

- **animica-bridge/**: Blockchain Bridge (Python)
  - Payment intent handling
  - Proof submission
  - Settlement engine

- **contributor-node/**: GPU Contributor Node (Python)
  - Resource advertising
  - Job execution
  - Proof generation

## Getting Started

Each package has its own README with specific setup instructions.

### Prerequisites
- Python 3.11+
- Node.js 20+
- Docker and Docker Compose
- kubectl (for Kubernetes deployment)

### Local Development

1. Install dependencies:
```bash
# Python packages
cd packages/<service>
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Node.js packages
cd packages/web
pnpm install
```

2. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your configuration
```

3. Run services:
```bash
# Using Docker Compose (recommended for local dev)
docker-compose -f docker-compose.compute.yml up

# Or individually
cd packages/<service>
python -m <service>.main
```

### Testing

```bash
# Python tests
pytest packages/<service>/tests/

# TypeScript tests
cd packages/web
pnpm test
```

### Deployment

See `infra/` directory for Terraform and Kubernetes configurations.

## Architecture

See `docs/compute-platform/ARCHITECTURE.md` for detailed architecture documentation.

## Contributing

1. Create a feature branch
2. Make your changes
3. Add tests
4. Run linters and tests
5. Submit a pull request

## License

See LICENSE.txt in the repository root.
