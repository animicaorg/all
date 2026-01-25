# BitGo Webhook Ingestor - Security Hardening

## Overview

The BitGo webhook ingestor service has been enhanced with comprehensive security hardening measures to protect against common attack vectors and ensure secure operation.

## Security Features

### 1. Webhook Signature Verification (HMAC-SHA256)

All incoming webhooks from BitGo are verified using HMAC-SHA256 signatures:

- **Implementation**: `src/http/middleware/webhook_verify.ts`
- **Algorithm**: HMAC-SHA256
- **Constant-time comparison**: Uses `crypto.timingSafeEqual` to prevent timing attacks
- **Header**: `x-bitgo-signature`

```typescript
// Webhook verification is automatically applied to all webhook routes
webhookRouter.use(
  createWebhookVerificationMiddleware(
    {
      webhookSecret: config.BITGO_WEBHOOK_SECRET,
      replayWindowSeconds: 300, // 5 minutes
      requireAuth: true,
    },
    logger
  )
);
```

### 2. Replay Attack Prevention

Timestamps in webhook payloads are verified to prevent replay attacks:

- **Window**: 5 minutes (configurable via `WEBHOOK_REPLAY_WINDOW_SECONDS`)
- **Timestamp sources**: `transfer.date` or `timestamp` field in payload
- **Validation**: Webhook is rejected if timestamp is outside the configured window

### 3. Secrets Management

Sensitive configuration is loaded using the `@cex/security/secrets` abstraction:

- **Default provider**: Environment variables
- **Extensible**: Can be upgraded to AWS Secrets Manager or GCP Secret Manager
- **Secrets loaded**:
  - `BITGO_WEBHOOK_SECRET` - Webhook signature verification
  - `BITGO_API_TOKEN` - BitGo API authentication
  - `ADMIN_KEY` - Admin endpoint authentication
  - `SERVICE_AUTH_KEY` - Service-to-service authentication

```typescript
import { configureSecrets, getSecret } from "@cex/security/secrets";

// Load secret at runtime
const webhookSecret = await getSecret("BITGO_WEBHOOK_SECRET");
```

### 4. Structured Logging with Redaction

All logs use structured logging from `@cex/observability` with automatic redaction:

- **Logger**: Pino-based structured logger
- **Automatic redaction**: Sensitive fields (passwords, tokens, keys) are automatically redacted
- **Request tracking**: Every request gets a unique `request_id` for tracing
- **Context**: Child loggers preserve context throughout request lifecycle

```typescript
// Sensitive data is automatically redacted
logger.info({ apiKey: "secret123" }); // Logs: { apiKey: "[REDACTED]" }
```

### 5. Rate Limiting

Rate limiting is applied to webhook endpoints to prevent abuse:

- **Backend**: Redis-based (with in-memory fallback)
- **Default limit**: 100 requests per minute (configurable)
- **Implementation**: Uses `@cex/middleware` rate limiter
- **Tracking**: By IP address

```typescript
// Rate limiting configuration
webhookRouter.use(
  createRateLimiter({
    windowMs: 60 * 1000,
    max: 100, // requests per minute
    keyPrefix: "webhook_rl",
    redis,
    logger,
  })
);
```

### 6. Admin Authentication

Admin endpoints require Bearer token authentication:

- **Header**: `Authorization: Bearer <admin_key>`
- **Key source**: `ADMIN_KEY` environment variable (loaded via secrets)
- **Endpoints**: All `/admin/*` routes
- **Validation**: Constant-time string comparison

### 7. Service Authentication (Optional)

Internal service-to-service calls can use JWT-based authentication:

- **Implementation**: `@cex/middleware/service_auth`
- **Algorithm**: HS256 JWT
- **Configuration**: `SERVICE_AUTH_KEY` environment variable
- **Usage**: For future internal API endpoints

## Configuration

### Environment Variables

```bash
# Service configuration
SERVICE_NAME=bitgo-webhook-ingestor
NODE_ENV=production
PORT=3000
LOG_LEVEL=info

# BitGo configuration (loaded from secrets)
BITGO_WEBHOOK_SECRET=your_webhook_secret_here
BITGO_API_TOKEN=your_api_token_here
BITGO_ENV=prod

# Security settings
WEBHOOK_RATE_LIMIT_PER_MINUTE=100
WEBHOOK_REPLAY_WINDOW_SECONDS=300

# Authentication
ADMIN_KEY=your_admin_key_here
SERVICE_AUTH_KEY=your_service_auth_key_here

# Database
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Redis (for rate limiting)
REDIS_URL=redis://localhost:6379

# NATS (for outbox)
NATS_URL=nats://localhost:4222
```

### Secrets Management Setup

#### Environment Variables (Default)

No additional setup required. Secrets are loaded from environment variables.

#### AWS Secrets Manager

```typescript
import { AwsSecretProvider } from "@cex/security/secrets";

configureSecrets(
  new AwsSecretProvider({
    region: "us-east-1",
    secretName: "bitgo-webhook-ingestor/prod",
  })
);
```

#### GCP Secret Manager

```typescript
import { GcpSecretProvider } from "@cex/security/secrets";

configureSecrets(
  new GcpSecretProvider({
    projectId: "my-project",
    prefix: "bitgo-webhook-ingestor-prod",
  })
);
```

## Security Best Practices

### 1. Webhook Secret Rotation

Rotate the `BITGO_WEBHOOK_SECRET` periodically:

1. Generate a new secret in BitGo console
2. Update the secret in your secrets manager
3. Restart the service to pick up the new secret
4. Remove the old secret from BitGo

### 2. Admin Key Management

- Use strong, randomly generated keys (minimum 32 characters)
- Rotate admin keys every 90 days
- Never commit keys to version control
- Use different keys per environment (dev/staging/prod)

### 3. Rate Limit Tuning

Monitor rate limit metrics and adjust based on traffic:

```bash
# Increase for high-traffic environments
WEBHOOK_RATE_LIMIT_PER_MINUTE=500

# Decrease for stricter protection
WEBHOOK_RATE_LIMIT_PER_MINUTE=50
```

### 4. Logging Best Practices

- Monitor logs for authentication failures
- Set up alerts for repeated failures (possible attack)
- Review logs regularly for suspicious patterns
- Ensure logs are shipped to a secure, centralized system

## Testing

### Test Webhook Signature Verification

```bash
# Generate HMAC signature
PAYLOAD='{"type":"transfer","transfer":{"id":"test123"}}'
SECRET="your_webhook_secret"
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" -hex | cut -d' ' -f2)

# Send test webhook
curl -X POST http://localhost:3000/webhook \
  -H "Content-Type: application/json" \
  -H "x-bitgo-signature: $SIGNATURE" \
  -d "$PAYLOAD"
```

### Test Rate Limiting

```bash
# Send multiple requests rapidly
for i in {1..120}; do
  curl -X POST http://localhost:3000/webhook \
    -H "Content-Type: application/json" \
    -H "x-bitgo-signature: valid_signature" \
    -d '{"type":"test"}' &
done
wait

# Should see 429 responses after limit is reached
```

### Test Admin Authentication

```bash
# Valid admin key
curl http://localhost:3000/admin/stats \
  -H "Authorization: Bearer your_admin_key"

# Invalid admin key (should fail)
curl http://localhost:3000/admin/stats \
  -H "Authorization: Bearer invalid_key"
```

## Monitoring

### Key Metrics

- **Webhook signature failures**: Track authentication failures
- **Rate limit hits**: Monitor abuse attempts
- **Request latency**: Ensure security measures don't impact performance
- **Admin access logs**: Audit admin operations

### Alert Conditions

Set up alerts for:

- High rate of signature verification failures (>5% of requests)
- Repeated rate limit violations from same IP
- Admin authentication failures
- Service errors or downtime

## Compliance

This implementation follows security best practices for:

- **OWASP Top 10**: Protection against injection, authentication failures, and security misconfigurations
- **PCI DSS**: Secure communication and access control for financial data
- **SOC 2**: Audit logging and access controls

## Support

For security concerns or questions, contact the security team.
