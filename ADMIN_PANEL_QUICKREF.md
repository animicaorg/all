# Admin Panel Quick Reference

## Quick Start (5 Minutes)

### 1. Database Setup
```bash
cd services/exchange-api
pnpm db:push  # Creates admin tables
```

### 2. Create Admin Account
```bash
cd services/admin-api
pnpm db:seed  # Creates admin@animica.io / Admin123!
```

### 3. Start Services
```bash
# Terminal 1: Admin API
cd services/admin-api && pnpm dev

# Terminal 2: Admin Web
cd apps/admin-web && pnpm dev
```

### 4. Login
- URL: http://localhost:5173
- Email: admin@animica.io
- Password: Admin123!
- TOTP: Use authenticator app with seed from step 2

## Architecture

### Components
- **services/admin-api** - REST API (port 4000)
- **apps/admin-web** - React UI (port 5173)
- **Shared database** - Uses exchange-api Prisma schema

### Security Stack
- **Auth**: JWT + refresh tokens + TOTP
- **Sessions**: Database-backed with revocation
- **RBAC**: 5 roles, 15+ permissions
- **Audit**: Every action logged with snapshots
- **Rate Limiting**: Per-IP and per-session
- **CSRF**: Token-based protection

## Roles & Permissions Matrix

| Permission | SUPERADMIN | OPS | COMPLIANCE | SUPPORT | READONLY |
|------------|------------|-----|------------|---------|----------|
| admins:* | ✓ | ✗ | ✗ | ✗ | ✗ |
| users:write | ✓ | ✓ | ✗ | ✗ | ✗ |
| users:freeze | ✓ | ✓ | ✓ | ✗ | ✗ |
| kyc:review | ✓ | ✗ | ✓ | ✗ | ✗ |
| markets:halt | ✓ | ✓ | ✗ | ✗ | ✗ |
| withdrawals:approve | ✓ | ✓ | ✓ | ✗ | ✗ |
| fees:write | ✓ | ✓ | ✗ | ✗ | ✗ |
| incidents:execute | ✓ | ✓ | ✗ | ✗ | ✗ |
| *:read | ✓ | ✓ | ✓ | ✓ | ✓ |

## API Endpoints

### Authentication
```
POST /admin/v1/auth/login        # Login with email/password/TOTP
POST /admin/v1/auth/logout       # Logout and revoke session
POST /admin/v1/auth/refresh      # Refresh access token
GET  /admin/v1/auth/me           # Get current admin info
```

### Users (Implemented)
```
GET  /admin/v1/users             # Search users
GET  /admin/v1/users/:id         # Get user details + balances
POST /admin/v1/users/:id/freeze  # Freeze account (requires reason)
POST /admin/v1/users/:id/unfreeze # Unfreeze account
```

### Health
```
GET /health                      # Simple health check
GET /admin/v1/health             # Detailed health with DB check
```

### TODO: Additional Endpoints
- Admins management (SUPERADMIN only)
- KYC review workflow
- Market controls (halt/resume/cancel-all)
- Fee schedules
- Wallet status (BitGo, Animica)
- Withdrawal approvals
- Incidents
- Audit log viewer

## Database Schema

### Admin Tables
```sql
admins              -- Admin accounts
admin_sessions      -- Session management
risk_flags          -- User risk flags
market_controls     -- Per-market settings
incident_actions    -- Incident response
audit_logs          -- Immutable audit trail (enhanced)
fee_schedules       -- Fee tiers (enhanced with creator)
withdrawal_approvals-- Multi-approver workflow (enhanced)
```

### Key Relationships
- `audit_logs.actorAdminId` → `admins.id`
- `risk_flags.createdBy` → `admins.id`
- `market_controls.updatedBy` → `admins.id`
- `withdrawal_approvals.approverAdminId` → `admins.id`

## Common Tasks

### Create New Admin
```typescript
// In admin-api
await prisma.admin.create({
  data: {
    email: 'ops@example.com',
    passwordHash: await hashPassword('SecurePass123!'),
    totpSecretEncrypted: generateTotpSecret(),
    role: 'OPS',
    status: 'ACTIVE',
  },
});
```

### Freeze User Account
```bash
curl -X POST http://localhost:4000/admin/v1/users/{userId}/freeze \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason": "Suspicious activity detected"}'
```

### View Audit Logs
```sql
SELECT 
  created_at,
  action,
  actor_admin_id,
  entity_type,
  entity_id,
  before,
  after
FROM audit_logs
WHERE actor_type = 'ADMIN'
ORDER BY created_at DESC
LIMIT 100;
```

## Environment Variables

### Required
```bash
DATABASE_URL="postgresql://..."  # Same as exchange-api
JWT_SECRET="[32+ chars]"          # Generate with openssl rand -hex 32
SESSION_SECRET="[32+ chars]"
CSRF_SECRET="[32+ chars]"
```

### Optional
```bash
REDIS_URL="redis://..."           # For rate limiting
ADMIN_WEB_URL="http://..."        # CORS origin
TOTP_WINDOW=2                     # TOTP tolerance (steps)
RATE_LIMIT_LOGIN_MAX=5            # Login attempts
```

## Security Checklist

### Development
- [x] Passwords hashed with Argon2
- [x] TOTP 2FA support
- [x] Session-based auth with refresh tokens
- [x] RBAC enforcement
- [x] Audit logging
- [x] Rate limiting
- [x] CSRF protection
- [x] HttpOnly cookies
- [ ] Redis for distributed rate limiting

### Production
- [ ] Strong secrets (32+ chars, random)
- [ ] HTTPS/TLS required
- [ ] Restrict CORS to admin domain only
- [ ] Configure firewall (IP whitelist)
- [ ] Enable Redis for rate limiting
- [ ] Set up log aggregation
- [ ] Configure monitoring & alerts
- [ ] Backup audit logs
- [ ] Review session timeout
- [ ] MFA enforced for all admins
- [ ] Regular security audits

## Troubleshooting

### "Connection refused" on login
Check admin-api is running on port 4000:
```bash
curl http://localhost:4000/health
```

### "Invalid credentials"
Verify admin exists:
```sql
SELECT id, email, role, status FROM admins;
```

### "Invalid TOTP token"
- Check clock sync
- Verify TOTP secret is correct
- Check TOTP_WINDOW setting

### "Forbidden" error
Check role has required permission:
```typescript
// In rbac.ts ROLE_PERMISSIONS mapping
ROLE_PERMISSIONS[admin.role].includes(permission)
```

### Database migration needed
```bash
cd services/exchange-api
pnpm db:push
```

## Testing

### Manual API Tests
```bash
# Login
curl -X POST http://localhost:4000/admin/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@animica.io","password":"Admin123!","totpToken":"123456"}'

# Use returned token
TOKEN="<access_token>"

# Get current admin
curl http://localhost:4000/admin/v1/auth/me \
  -H "Authorization: Bearer $TOKEN"

# Search users
curl http://localhost:4000/admin/v1/users?query=test \
  -H "Authorization: Bearer $TOKEN"

# Health check
curl http://localhost:4000/admin/v1/health
```

### Unit Tests (TODO)
```bash
cd services/admin-api
pnpm test
```

### UI Tests (TODO)
```bash
cd apps/admin-web
pnpm test
```

## Monitoring

### Key Metrics
- Login failures per IP
- Session count
- Active admin count
- Audit log growth rate
- API response times
- Failed permission checks

### Health Endpoints
- `GET /health` - Basic health
- `GET /admin/v1/health` - Detailed with DB check

### Logs
All logs include:
- Request ID (correlation)
- Timestamp
- Level (info/warn/error)
- Admin ID (if authenticated)
- Action performed

## Next Steps

### Phase 1 (Complete) ✅
- Database schema
- Auth & RBAC middleware
- Login/logout flow
- User management routes
- Basic admin web UI

### Phase 2 (In Progress) 🚧
- KYC review workflow
- Market controls
- Fee management
- Wallet status pages
- Withdrawal approvals

### Phase 3 (TODO) ⏳
- Incident management
- Audit log viewer
- Admin management UI
- Comprehensive tests
- Docker deployment

## Support

### Documentation
- [ADMIN_PANEL_README.md](./ADMIN_PANEL_README.md) - Full setup guide
- [services/admin-api/README.md](./services/admin-api/README.md) - API docs
- [apps/admin-web/README.md](./apps/admin-web/README.md) - UI docs

### Code Structure
```
services/admin-api/src/
├── config.ts               # Environment config
├── index.ts                # Entry point
├── http/
│   ├── server.ts           # Express app
│   ├── middleware/         # Auth, RBAC, audit, etc.
│   └── routes/             # Route handlers
├── services/               # Business logic
└── utils/                  # Helpers

apps/admin-web/src/
├── App.tsx                 # React app
├── components/             # Reusable components
├── contexts/               # Auth context
├── pages/                  # Page components
└── services/               # API client
```

## Quick Commands

```bash
# Install everything
pnpm install

# Generate Prisma client
cd services/admin-api && pnpm db:generate

# Seed admin
cd services/admin-api && pnpm db:seed

# Start admin API
cd services/admin-api && pnpm dev

# Start admin web
cd apps/admin-web && pnpm dev

# Build for production
cd services/admin-api && pnpm build
cd apps/admin-web && pnpm build

# Run tests (when implemented)
pnpm test

# Format code
pnpm format

# Lint code
pnpm lint
```

---

**Last Updated**: 2026-01-25  
**Version**: 0.1.0  
**Status**: Phase 1 Complete, Phase 2 In Progress
