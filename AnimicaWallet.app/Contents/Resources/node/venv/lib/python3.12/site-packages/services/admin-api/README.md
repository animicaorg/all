# Admin API Service

Secure operations and administration API for the Animica Centralized Exchange.

## Features

- **Authentication**: Email/password + TOTP 2FA
- **RBAC**: Role-based access control with fine-grained permissions
- **Audit Logging**: Comprehensive audit trail for all admin actions
- **Rate Limiting**: Protection against brute force and abuse
- **Session Management**: Secure session handling with refresh tokens

## Roles & Permissions

### Roles
- **SUPERADMIN**: Full system access
- **OPS**: Operations (markets, withdrawals, users)
- **COMPLIANCE**: KYC review, risk management
- **SUPPORT**: Read-only + user support
- **READONLY**: View-only access

### Permissions
- `users:read`, `users:write`, `users:freeze`
- `kyc:read`, `kyc:review`
- `markets:read`, `markets:write`, `markets:halt`
- `fees:read`, `fees:write`
- `withdrawals:read`, `withdrawals:approve`, `withdrawals:sign`
- `incidents:read`, `incidents:execute`
- `audit:read`
- `admins:read`, `admins:write`
- `wallets:read`

## Setup

### Prerequisites
- Node.js 18.17+
- PostgreSQL 14+
- Redis (optional, for rate limiting)

### Installation

```bash
# Install dependencies
pnpm install

# Set up environment
cp .env.example .env
# Edit .env with your configuration
# Required additions:
# - ADMIN_BOOTSTRAP_SECRET (first admin bootstrap)
# - CONFIG_ENCRYPTION_KEY (32-byte key for encrypting BitGo secrets)

# Generate Prisma client
pnpm db:generate

# Run migrations (from exchange-api)
cd ../exchange-api
pnpm db:migrate
```

### Creating the First Admin

```bash
# Option 1: Bootstrap on first login
# 1) Set ADMIN_BOOTSTRAP_SECRET in .env
# 2) Open the admin login page and expand "First-time setup"
# 3) Provide email, password, and the bootstrap secret
# 4) A SUPERADMIN account is created and logged in
```

```bash
# Option 2: Run the seed script
pnpm db:seed
```

### Development

```bash
# Start dev server with hot reload
pnpm dev

# Build for production
pnpm build

# Start production server
pnpm start

# Run tests
pnpm test
```

## API Endpoints

### Authentication
- `POST /admin/v1/auth/login` - Login with email, password, TOTP
- `POST /admin/v1/auth/logout` - Logout and revoke session
- `POST /admin/v1/auth/refresh` - Refresh access token
- `GET /admin/v1/auth/me` - Get current admin info

### Admin Management (SUPERADMIN only)
- `GET /admin/v1/admins` - List admins
- `POST /admin/v1/admins` - Create admin
- `PATCH /admin/v1/admins/:id` - Update admin
- `POST /admin/v1/admins/:id/reset-password` - Reset password
- `POST /admin/v1/admins/:id/rotate-totp` - Rotate TOTP secret

### Users
- `GET /admin/v1/users` - Search/list users
- `GET /admin/v1/users/:id` - Get user details
- `POST /admin/v1/users/:id/freeze` - Freeze user account
- `POST /admin/v1/users/:id/unfreeze` - Unfreeze user account
- `GET /admin/v1/users/:id/audit` - Get user audit log

### KYC (COMPLIANCE)
- `GET /admin/v1/kyc/queue` - KYC review queue
- `GET /admin/v1/kyc/:userId` - Get KYC case details
- `POST /admin/v1/kyc/:userId/approve` - Approve KYC
- `POST /admin/v1/kyc/:userId/reject` - Reject KYC
- `POST /admin/v1/kyc/:userId/request-info` - Request more info

### Markets (OPS)
- `GET /admin/v1/markets` - List markets
- `PATCH /admin/v1/markets/:marketId` - Update market controls
- `POST /admin/v1/markets/:marketId/halt` - Halt trading
- `POST /admin/v1/markets/:marketId/resume` - Resume trading
- `POST /admin/v1/markets/:marketId/cancel-all` - Cancel all orders

### Fees (OPS/SUPERADMIN)
- `GET /admin/v1/fees/schedules` - List fee schedules
- `POST /admin/v1/fees/schedules` - Create fee schedule
- `PATCH /admin/v1/fees/schedules/:id` - Update fee schedule
- `PUT /admin/v1/fees/markets/:marketId/override` - Set market fee override

### Wallets (OPS/READONLY)
- `GET /admin/v1/wallets/summary` - Wallet balances summary
- `GET /admin/v1/wallets/bitgo` - BitGo policy & status
- `GET /admin/v1/wallets/animica` - Animica node health

### Settings (OPS/SUPERADMIN)
- `GET /admin/v1/settings/bitgo` - Get BitGo configuration (masked secrets)
- `PUT /admin/v1/settings/bitgo` - Update BitGo configuration
- `POST /admin/v1/settings/bitgo/test` - Test BitGo connection

### Withdrawals (OPS)
- `GET /admin/v1/withdrawals` - List withdrawals
- `GET /admin/v1/withdrawals/:id` - Get withdrawal details
- `POST /admin/v1/withdrawals/:id/approve` - Approve withdrawal
- `POST /admin/v1/withdrawals/:id/deny` - Deny withdrawal
- `POST /admin/v1/withdrawals/:id/force-retry` - Force retry

### Incidents (OPS/SUPERADMIN)
- `POST /admin/v1/incidents` - Create incident
- `POST /admin/v1/incidents/:id/freeze-market` - Freeze market
- `POST /admin/v1/incidents/:id/freeze-user` - Freeze user
- `POST /admin/v1/incidents/:id/pause-withdrawals` - Pause withdrawals
- `GET /admin/v1/incidents/:id` - Get incident details

### Audit
- `GET /admin/v1/audit` - Query audit logs

### Health
- `GET /health` - Health check
- `GET /admin/v1/health` - Detailed health check

## Security

### Authentication Flow
1. Admin submits email + password + TOTP
2. Server verifies credentials and TOTP
3. Server creates session and returns JWT access token + refresh token
4. Access token expires in 1 hour, refresh token in 7 days
5. Client uses refresh token to get new access token
6. On logout, session is revoked

### Session Security
- Sessions stored in database
- Refresh tokens hashed with Argon2
- Sessions can be revoked individually or all at once
- Sessions expire after configured period

### CSRF Protection
- CSRF tokens required for state-changing operations
- Tokens validated via middleware

### Rate Limiting
- Login attempts: 5 per IP/email per 5 minutes
- Admin endpoints: 60 requests per session per minute
- Failed login attempts logged for monitoring

### Audit Trail
Every admin action is logged with:
- Actor admin ID and role
- Action type and target entity
- Before/after snapshots (PII redacted)
- Request ID, IP, user agent
- Timestamp

## BitGo Configuration (Admin Portal)

1. Navigate to **Settings → BitGo** in the admin web console.
2. Enter the environment, API base URL (optional), wallet IDs, and secrets.
3. Click **Save Settings** to persist the encrypted config.
4. Use **Test Connection** to verify BitGo connectivity.

## Deployment

### Docker

```bash
# Build image
docker build -t animica/admin-api .

# Run container
docker run -p 4000:4000 \
  -e DATABASE_URL="..." \
  -e JWT_SECRET="..." \
  -e REDIS_URL="..." \
  animica/admin-api
```

### Environment Variables

See `.env.example` for all available configuration options.

Required variables:
- `DATABASE_URL`
- `JWT_SECRET`
- `SESSION_SECRET`
- `CSRF_SECRET`

## Development

### Project Structure

```
src/
├── config.ts           # Configuration loader
├── index.ts            # Main entry point
├── db/
│   └── prisma.ts       # Prisma client
├── http/
│   ├── server.ts       # Express server
│   ├── middleware/     # Middleware
│   │   ├── auth.ts     # Authentication
│   │   ├── rbac.ts     # RBAC enforcement
│   │   ├── audit.ts    # Audit logging
│   │   └── ...
│   └── routes/         # Route handlers
│       ├── auth.ts     # Authentication
│       ├── users.ts    # User management
│       ├── kyc.ts      # KYC review
│       └── ...
├── services/           # Business logic
│   ├── auth.ts         # Auth service
│   └── ...
├── clients/            # External service clients
│   ├── exchange_api.ts
│   ├── bitgo.ts
│   └── animica.ts
└── utils/              # Utilities
    ├── logger.ts
    └── crypto.ts
```

### Testing

```bash
# Run all tests
pnpm test

# Watch mode
pnpm test:watch

# With coverage
pnpm test -- --coverage
```

## License

Apache 2.0
