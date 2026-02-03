# CEX (Centralized Exchange) Services

This directory contains the CEX microservices architecture built with TypeScript and pnpm workspaces.

## Architecture

The CEX is organized as a monorepo with the following structure:

- **packages/** - Shared libraries used across services
  - `@cex/common` - Common utilities, logging, config, database, and messaging
  - `@cex/db` - Database schemas and migrations
  - `@cex/middleware` - Express middleware
  - `@cex/security` - Security utilities
  - `@cex/observability` - Monitoring and observability

- **services/** - Microservices
  - `api-gateway` - Main API gateway
  - `admin-service` - Admin panel backend
  - `auth-service` - Authentication service
  - `ledger-service` - Ledger and balance management
  - `matching-engine` - Order matching
  - `wallet-router` - Wallet operations
  - `risk-service` - Risk management
  - `withdrawals-service` - Withdrawal processing
  - `animica-indexer` - Blockchain indexer
  - `animica-asset-service` - Asset service
  - `bitgo-webhook-ingestor` - BitGo webhook handler

## Prerequisites

- Node.js >= 20
- pnpm >= 9.12.1

## Getting Started

### 1. Install pnpm (if not already installed)

```bash
npm install -g pnpm@9.12.1
```

### 2. Install dependencies

From the `cex/` directory:

```bash
pnpm install
```

### 3. Build shared packages

**IMPORTANT**: You must build shared packages before running services that depend on them.

```bash
# Build all packages
pnpm -r build

# Or build a specific package
pnpm --filter @cex/common build
```

### 4. Run services

```bash
# Run all services in development mode
pnpm dev

# Run a specific service
pnpm --filter @cex/api-gateway dev
```

## Build Order

Due to workspace dependencies, packages must be built in order:

1. `@cex/common` (no dependencies on other workspace packages)
2. `@cex/db` (depends on @cex/common)
3. Other packages and services (depend on common packages)

The root `pnpm -r build` command handles this automatically.

## Common Issues

### "Cannot find package '@cex/common/dist/index.js'"

This error occurs when the `@cex/common` package hasn't been built yet. Run:

```bash
pnpm --filter @cex/common build
```

Or build all packages:

```bash
pnpm -r build
```

### Missing TypeScript declarations

If you see TypeScript errors about missing type definitions:

1. Ensure the package has been built with `pnpm build`
2. Check that `tsconfig.base.json` has `"declaration": true`
3. Verify the package's `dist/` directory contains `.d.ts` files

## Development

### Adding a new service

1. Create a new directory under `services/`
2. Add a `package.json` with workspace dependencies (e.g., `"@cex/common": "workspace:*"`)
3. Create a `tsconfig.json` that extends `../../tsconfig.base.json`
4. Build required workspace dependencies before starting development

## How to Use (Admin & BitGo)

- **Admin bootstrap**: Configure `ADMIN_BOOTSTRAP_SECRET` for the admin API, then use the admin login page’s “First-time setup” toggle to initialize the first SUPERADMIN.
- **BitGo configuration**: Use the admin portal **Settings → BitGo** page to edit environment, API base URL, and wallet settings.
- **Test BitGo**: Use the **Test Connection** button on the BitGo settings page to verify credentials.

### Adding a new shared package

1. Create a new directory under `packages/`
2. Add a `package.json` with a scoped name (e.g., `@cex/new-package`)
3. Include a `build` script: `"build": "tsc -p tsconfig.json"`
4. Create a `tsconfig.json` that extends `../../tsconfig.base.json`
5. Ensure other packages/services reference it as `"@cex/new-package": "workspace:*"`

## Scripts

From the root `cex/` directory:

- `pnpm dev` - Run all services in development mode (parallel)
- `pnpm build` - Build all packages and services
- `pnpm lint` - Lint all packages and services
- `pnpm migrate` - Run database migrations
- `pnpm seed` - Seed the database

## Environment Variables

Each service requires its own environment configuration. See individual service directories for `.env.example` files.

Common environment variables (from `@cex/common`):

- `NATS_URL` - NATS messaging server URL
- `REDIS_URL` - Redis connection URL
- `DB_HOST` - PostgreSQL host
- `DB_PORT` - PostgreSQL port (default: 5432)
- `DB_USER` - PostgreSQL username
- `DB_PASSWORD` - PostgreSQL password
- `DB_NAME` - PostgreSQL database name

### Service ports & host binding

- `PORT` - HTTP port for each service (service-specific defaults: `api-gateway` 3000, `admin-service` 4000)
- `HOST` - Bind address for each service (default: `0.0.0.0`)

When using `./cex_up`, configure ports in `cex/ops/env/.env`:

- `API_GATEWAY_PORT` (defaults to 3000)
- `ADMIN_SERVICE_PORT` (defaults to 4000)

To expose the Vite admin web UI on your network, run with `CEX_EXPOSE=1` so it passes `--host 0.0.0.0`.

## Testing

```bash
# Run all tests
pnpm -r test

# Run tests for a specific package/service
pnpm --filter @cex/common test
```

## Troubleshooting

1. **Module resolution errors**: Ensure all workspace dependencies are built
2. **Type errors**: Run `pnpm -r build` to regenerate TypeScript declarations
3. **Dependency issues**: Try `pnpm install --force` to reinstall dependencies
4. **Port conflicts**: Check that required ports are not already in use

## License

See the root LICENSE file.
