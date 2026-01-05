# Animica Compute Platform - Authentication Service

FastAPI-based authentication and authorization service with support for email/password and wallet signature authentication.

## Features

- **Email/Password Authentication**: Standard JWT-based auth
- **Wallet Signature Authentication**: Sign-in with Animica wallet (Dilithium3)
- **Organizations & Workspaces**: Multi-tenancy support
- **Role-Based Access Control (RBAC)**: Owner, Admin, Member roles
- **API Key Management**: Generate and validate API keys
- **Audit Logging**: Track all authentication events
- **Session Management**: Redis-backed session storage
- **OAuth2 Integration**: Google and GitHub (optional)

## API Endpoints

### Authentication
- `POST /auth/register` - Register new user with email/password
- `POST /auth/login` - Login with email/password
- `POST /auth/wallet/challenge` - Get challenge for wallet signature
- `POST /auth/wallet/verify` - Verify wallet signature and login
- `POST /auth/refresh` - Refresh JWT access token
- `POST /auth/logout` - Logout and invalidate session
- `GET /auth/me` - Get current user profile

### Organizations
- `GET /orgs` - List user's organizations
- `POST /orgs` - Create new organization
- `GET /orgs/{org_id}` - Get organization details
- `PATCH /orgs/{org_id}` - Update organization
- `DELETE /orgs/{org_id}` - Delete organization

### Organization Members
- `GET /orgs/{org_id}/members` - List members
- `POST /orgs/{org_id}/members` - Invite member
- `PATCH /orgs/{org_id}/members/{user_id}` - Update member role
- `DELETE /orgs/{org_id}/members/{user_id}` - Remove member

### API Keys
- `GET /api-keys` - List user's API keys
- `POST /api-keys` - Create new API key
- `DELETE /api-keys/{key_id}` - Revoke API key

### Audit Logs
- `GET /audit-logs` - List audit logs (admin only)

## Database Schema

### Users Table
```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255),
    wallet_address VARCHAR(255) UNIQUE,
    wallet_public_key TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    is_verified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### Organizations Table
```sql
CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) UNIQUE NOT NULL,
    owner_id UUID REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### Organization Members Table
```sql
CREATE TABLE organization_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) NOT NULL, -- 'owner', 'admin', 'member'
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(organization_id, user_id)
);
```

### API Keys Table
```sql
CREATE TABLE api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    organization_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    key_hash VARCHAR(255) UNIQUE NOT NULL,
    prefix VARCHAR(20) NOT NULL,
    last_used_at TIMESTAMP,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE
);
```

### Audit Logs Table
```sql
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    action VARCHAR(255) NOT NULL,
    resource_type VARCHAR(100),
    resource_id VARCHAR(255),
    ip_address VARCHAR(45),
    user_agent TEXT,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

## Development

### Setup
```bash
cd packages/auth-service
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run Migrations
```bash
alembic upgrade head
```

### Run Development Server
```bash
uvicorn auth_service.main:app --reload --host 0.0.0.0 --port 8001
```

### Run Tests
```bash
pytest tests/ -v
```

## Environment Variables

See `.env.compute.example` in repository root for required configuration.

Key variables:
- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string
- `JWT_SECRET_KEY`: Secret for JWT signing
- `JWT_ALGORITHM`: JWT algorithm (default: HS256)
- `JWT_EXPIRATION_MINUTES`: Access token expiration (default: 15)
- `REFRESH_TOKEN_EXPIRATION_DAYS`: Refresh token expiration (default: 30)
- `ANIMICA_RPC_URL`: Animica blockchain RPC endpoint

## Authentication Flow

### Email/Password Flow
1. User submits email + password to `/auth/register`
2. Service creates user with bcrypt password hash
3. User receives verification email (optional)
4. User logs in via `/auth/login` with credentials
5. Service returns JWT access token + refresh token
6. Client includes JWT in `Authorization: Bearer <token>` header

### Wallet Signature Flow
1. User requests challenge via `/auth/wallet/challenge` with wallet address
2. Service generates random nonce and stores in Redis (5 min TTL)
3. User signs challenge with Dilithium3 private key
4. User submits signature via `/auth/wallet/verify`
5. Service verifies signature using public key from wallet address
6. Service returns JWT access token + refresh token

### API Key Flow
1. User generates API key via `/api-keys`
2. Service returns key with prefix `anm_` (show once)
3. User includes key in `X-API-Key` header
4. Service validates key and loads user context

## Security Features

- Passwords hashed with bcrypt (cost factor 12)
- JWT tokens include user ID, org ID, and expiration
- Refresh tokens stored in Redis with automatic cleanup
- API keys hashed before storage
- Rate limiting on auth endpoints
- Challenge-response for wallet authentication prevents replay
- Audit logging for all sensitive operations
- Input validation via Pydantic models
- SQL injection prevention via SQLAlchemy ORM

## RBAC Permissions

### Owner
- All admin permissions
- Delete organization
- Transfer ownership
- View billing information

### Admin
- All member permissions
- Manage members (invite, remove, change roles)
- Manage API keys
- Update organization settings

### Member
- View organization details
- Use organization resources
- View own usage

## Monitoring

- Health check: `GET /health`
- Metrics: `GET /metrics` (Prometheus format)
- Logs: Structured JSON logging to stdout
