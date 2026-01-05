# Animica Compute Platform - API Gateway

FastAPI-based API Gateway for routing requests to backend services.

## Features

- Request routing to backend microservices
- Authentication and authorization middleware
- Rate limiting per tenant
- CORS configuration
- WebSocket support for streaming
- Health checks and monitoring

## API Endpoints

### Authentication
- `POST /v1/auth/register` - Register new user
- `POST /v1/auth/login` - Login with email/password
- `POST /v1/auth/wallet` - Login with wallet signature
- `POST /v1/auth/refresh` - Refresh JWT token
- `GET /v1/auth/me` - Get current user info

### LLM Inference (OpenAI-compatible)
- `POST /v1/chat/completions` - Chat completion (streaming supported)
- `POST /v1/completions` - Text completion
- `POST /v1/embeddings` - Generate embeddings
- `GET /v1/models` - List available models

### Code Execution
- `POST /v1/code/execute` - Execute code in sandbox
- `GET /v1/code/languages` - List supported languages
- `GET /v1/code/packages` - List available packages

### Billing
- `GET /v1/billing/balance` - Get credit balance
- `GET /v1/billing/usage` - Get usage history
- `POST /v1/billing/payment-method` - Add payment method
- `POST /v1/billing/subscribe` - Subscribe to plan

### Marketplace
- `POST /v1/marketplace/jobs` - Submit compute job
- `GET /v1/marketplace/jobs/{job_id}` - Get job status
- `GET /v1/marketplace/providers` - List providers
- `GET /v1/marketplace/earnings` - Get provider earnings

### GitHub
- `POST /webhooks/github` - GitHub webhook endpoint
- `GET /v1/github/installations` - List installations
- `POST /v1/github/review` - Trigger manual review

## Development

### Setup

```bash
cd packages/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run

```bash
# Development mode (auto-reload)
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

# Production mode
uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Environment Variables

See `.env.example` in repository root.

### Testing

```bash
pytest tests/ -v
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     API Gateway                          │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │          Middleware Stack                          │ │
│  │  - CORS                                            │ │
│  │  - Authentication (JWT validation)                │ │
│  │  - Rate Limiting (Redis-backed)                   │ │
│  │  - Logging & Tracing                              │ │
│  │  - Error Handling                                 │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │          Route Handlers                            │ │
│  │  /v1/auth/*        → Auth Service                │ │
│  │  /v1/chat/*        → Inference Service            │ │
│  │  /v1/code/*        → Sandbox Runner               │ │
│  │  /v1/billing/*     → Billing Service              │ │
│  │  /v1/marketplace/* → Animica Bridge               │ │
│  └────────────────────────────────────────────────────┘ │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## Configuration

### Rate Limiting

Default limits:
- Free tier: 100 requests/minute
- Pro tier: 1000 requests/minute
- Enterprise: Custom limits

### CORS

Allowed origins are configured via `CORS_ORIGINS` environment variable.

### Authentication

Supports multiple auth methods:
1. JWT Bearer token
2. API Key (header: `X-API-Key`)
3. Wallet signature (for blockchain operations)

## Monitoring

- Prometheus metrics at `/metrics`
- Health check at `/health`
- API documentation at `/docs` (Swagger UI)
- OpenAPI spec at `/openapi.json`

## Error Handling

Standard error response format:

```json
{
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Rate limit exceeded. Try again in 60 seconds.",
    "details": {
      "limit": 100,
      "window": "1m",
      "retry_after": 60
    }
  }
}
```

## Security

- All endpoints use TLS 1.3 in production
- Sensitive data never logged
- Input validation via Pydantic models
- SQL injection prevention via parameterized queries
- XSS prevention via CSP headers

## Performance

- Async/await for all I/O operations
- Connection pooling for databases
- Redis caching for hot paths
- Circuit breakers for downstream services

## Deployment

See `infra/kubernetes/api-gateway/` for Kubernetes manifests.

Docker image: `animica/api-gateway:latest`
