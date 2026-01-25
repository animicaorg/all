# Admin Panel + Operations Tooling

Secure operations and administration system for the Animica Centralized Exchange.

## Components

- **services/admin-api**: Backend REST API for admin operations
- **apps/admin-web**: React-based admin console UI

## Quick Start

### 1. Set up Database

```bash
cd services/exchange-api

# Run migrations (creates admin tables)
pnpm db:push

# Or create and run migrations
pnpm db:migrate
```

### 2. Create Initial Admin

```bash
cd services/admin-api

# Create first SUPERADMIN account
pnpm db:seed

# This will output credentials:
# Email: admin@animica.io
# Password: Admin123!
# TOTP Secret: <base32-encoded-secret>
```

Add the TOTP secret to your authenticator app (Google Authenticator, Authy, etc.).

### 3. Start Admin API

```bash
cd services/admin-api

# Copy environment template
cp .env.example .env

# Edit .env with your configuration
# - DATABASE_URL (same as exchange-api)
# - JWT_SECRET (generate secure random string)
# - SESSION_SECRET (generate secure random string)
# - CSRF_SECRET (generate secure random string)

# Install dependencies (from repo root)
cd ../..
pnpm install

# Start the API
cd services/admin-api
pnpm dev

# Server runs on http://localhost:4000
```

### 4. Start Admin Web UI

```bash
cd apps/admin-web

# Start dev server
pnpm dev

# UI runs on http://localhost:5173
```

### 5. Login

1. Navigate to http://localhost:5173
2. Enter credentials from seed script
3. Enter TOTP code from authenticator app
4. You should be redirected to the dashboard

## Features

### Authentication & Security
- ✅ Email/password + TOTP 2FA
- ✅ JWT-based sessions with refresh tokens
- ✅ Role-based access control (RBAC)
- ✅ Comprehensive audit logging
- ✅ Rate limiting & brute force protection
- ✅ CSRF protection
- ✅ HttpOnly secure cookies

### Roles & Permissions
- **SUPERADMIN**: Full system access
- **OPS**: Operations (markets, withdrawals, users)
- **COMPLIANCE**: KYC review, risk management
- **SUPPORT**: Read-only + user support
- **READONLY**: View-only access

### Core Features
- ✅ Admin management (SUPERADMIN only)
- ✅ User management (search, freeze/unfreeze, view details)
- 🚧 KYC review workflow (queue, approve/reject, request info)
- 🚧 Market controls (halt/resume, cancel-all orders)
- 🚧 Fee schedule management
- 🚧 Wallet status (BitGo policy, Animica node health)
- 🚧 Withdrawal approvals (multi-approver workflow)
- 🚧 Incident management
- 🚧 Audit log viewer

✅ = Implemented  
🚧 = Partially implemented / UI placeholder

## Architecture

### Database Schema

New tables added to exchange-api schema:
- `admins` - Admin accounts
- `admin_sessions` - Session management
- `risk_flags` - User risk flags
- `market_controls` - Market-level controls
- `incident_actions` - Incident response actions
- Enhanced `audit_log` with admin tracking
- Enhanced `fee_schedules` with admin metadata
- Enhanced `withdrawal_approvals` with admin approvers

### Admin API

Built with:
- Express + TypeScript
- Prisma (shares schema with exchange-api)
- Argon2 (password hashing)
- Speakeasy (TOTP)
- JWT (access tokens)
- Redis (rate limiting, optional)

Middleware stack:
1. Request ID assignment
2. CORS & security headers (Helmet)
3. Audit logging context
4. Authentication (JWT verification)
5. RBAC enforcement
6. Rate limiting
7. Error handling

### Admin Web

Built with:
- React 18 + TypeScript
- Vite (build tool)
- React Router v6
- Tailwind CSS
- Zustand + React Query
- Axios

## API Endpoints

See `services/admin-api/README.md` for full API documentation.

### Authentication
- `POST /admin/v1/auth/login` - Login
- `POST /admin/v1/auth/logout` - Logout
- `POST /admin/v1/auth/refresh` - Refresh token
- `GET /admin/v1/auth/me` - Current admin info

### Admin Management (SUPERADMIN)
- `GET /admin/v1/admins` - List admins
- `POST /admin/v1/admins` - Create admin
- `PATCH /admin/v1/admins/:id` - Update admin
- `POST /admin/v1/admins/:id/reset-password` - Reset password
- `POST /admin/v1/admins/:id/rotate-totp` - Rotate TOTP secret

### Users
- `GET /admin/v1/users` - Search/list users
- `GET /admin/v1/users/:id` - Get user details
- `POST /admin/v1/users/:id/freeze` - Freeze account
- `POST /admin/v1/users/:id/unfreeze` - Unfreeze account

(More routes to be implemented)

## Security Considerations

### Password Requirements
- Minimum 8 characters
- Hashed with Argon2id
- Argon2 parameters: memory=19MiB, time=2, parallelism=1

### TOTP Requirements
- 6-digit codes
- 30-second time step
- Window of ±2 steps (60 seconds)
- Base32-encoded secrets

### Session Management
- Access tokens expire in 1 hour
- Refresh tokens expire in 7 days
- Refresh tokens hashed before storage
- Sessions can be revoked individually or all at once
- Session metadata includes IP and user agent

### Rate Limiting
- Login: 5 attempts per IP/email per 5 minutes
- Admin endpoints: 60 requests per session per minute
- Failed attempts logged for monitoring

### Audit Trail
Every admin action is logged with:
- Actor admin ID and role
- Action type and target entity
- Before/after snapshots (PII redacted as needed)
- Request ID, IP, user agent
- Timestamp

Audit logs are immutable and indexed for fast queries.

## Deployment

### Docker Compose

```yaml
services:
  admin-api:
    build: ./services/admin-api
    ports:
      - "4000:4000"
    environment:
      DATABASE_URL: postgresql://user:pass@postgres:5432/exchange_api
      JWT_SECRET: ${JWT_SECRET}
      SESSION_SECRET: ${SESSION_SECRET}
      CSRF_SECRET: ${CSRF_SECRET}
      REDIS_URL: redis://redis:6379/1
    depends_on:
      - postgres
      - redis
```

### Production Checklist

- [ ] Use strong secrets (min 32 characters, randomly generated)
- [ ] Enable HTTPS/TLS
- [ ] Configure proper CORS origins
- [ ] Set up Redis for rate limiting
- [ ] Configure log aggregation
- [ ] Set up monitoring & alerting
- [ ] Configure firewall to restrict API access
- [ ] Review and adjust rate limits
- [ ] Set up backup for audit logs
- [ ] Configure session timeout appropriately
- [ ] Enable MFA for all admins
- [ ] Review RBAC permissions
- [ ] Set up incident response procedures

## Development

### Adding a New Route

1. Create service in `services/admin-api/src/services/`
2. Create route handler in `services/admin-api/src/http/routes/`
3. Apply middleware (auth, RBAC, validation)
4. Add audit logging for state-changing operations
5. Mount router in `server.ts`
6. Update tests
7. Document in README

### Adding a New Page

1. Create page component in `apps/admin-web/src/pages/`
2. Add route to `App.tsx`
3. Add navigation item to `Layout.tsx`
4. Create API methods in `services/api.ts`
5. Implement permission checks if needed
6. Test with different roles

## Testing

### Admin API

```bash
cd services/admin-api
pnpm test
```

### Admin Web

```bash
cd apps/admin-web
pnpm test
```

## Troubleshooting

### "Database migration needed"
Run migrations from exchange-api:
```bash
cd services/exchange-api
pnpm db:push
```

### "Invalid credentials" on login
Verify the admin exists in database and TOTP is correct.

### "CORS error"
Check `ADMIN_WEB_URL` in admin-api `.env` matches the UI URL.

### "Session expired"
Sessions expire after 7 days. Login again.

## License

Apache 2.0
