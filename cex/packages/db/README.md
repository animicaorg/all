# @cex/db

Database schema, migrations, and seeds for the CEX platform.

## Prerequisites

- PostgreSQL 16+ running and accessible
- Node.js 20+
- pnpm 9+

## Environment Variables

The following environment variables are required:

- `DB_HOST` - PostgreSQL host (e.g., `localhost` or `postgres`)
- `DB_PORT` - PostgreSQL port (default: `5432`)
- `DB_USER` - PostgreSQL username (e.g., `cex`)
- `DB_PASSWORD` - PostgreSQL password
- `DB_NAME` - Database name (e.g., `cex_exchange`)

## Setup

### Option 1: Using Docker Compose (Recommended)

The easiest way to run the database and seed it is via Docker Compose:

```bash
# From the root of the repository
./cex_up
```

This script will:
1. Create `.env` from `.env.example` if it doesn't exist
2. Start PostgreSQL, Redis, and NATS containers
3. Run migrations automatically
4. Seed the database
5. Start the CEX services

### Option 2: Manual Setup

If you're running PostgreSQL locally:

1. **Create `.env` file in `cex/ops/env/`:**

```bash
cd cex/ops/env
cp .env.example .env
```

2. **Update the `.env` file with your database credentials:**

```bash
DB_HOST=localhost
DB_PORT=5432
DB_USER=your_postgres_user
DB_PASSWORD=your_postgres_password
DB_NAME=cex_exchange
```

3. **Create the database:**

```bash
# Connect to PostgreSQL and create the database
# Note: You may need to provide a password or configure peer authentication
# For peer authentication, you may need to run as the postgres user:
# sudo -u postgres createdb cex_exchange

# Or with password authentication:
createdb -U your_postgres_user cex_exchange

# Alternatively, use psql:
psql -U your_postgres_user -c "CREATE DATABASE cex_exchange;"
```

4. **Run migrations:**

```bash
cd /path/to/cex
pnpm --filter @cex/db migrate
```

5. **Seed the database:**

```bash
pnpm --filter @cex/db seed
```

## Troubleshooting

### Error: Missing required environment variables

If you see this error:

```
❌ Error: Missing required environment variables for database connection:
   - DB_HOST
   - DB_USER
   ...
```

**Solution:** Ensure your environment variables are set. If using Docker Compose, check that `cex/ops/env/.env` exists. If running manually, source your `.env` file or export the variables:

```bash
export DB_HOST=localhost
export DB_USER=cex
export DB_PASSWORD=cex_password
export DB_NAME=cex_exchange
```

### Error: ECONNREFUSED

If you see `AggregateError [ECONNREFUSED]`:

**Causes:**
1. PostgreSQL is not running
2. PostgreSQL is not accessible at the specified host/port
3. Firewall is blocking the connection

**Solutions:**
- **Docker Compose:** Ensure services are running with `docker compose ps`
- **Local PostgreSQL:** Ensure PostgreSQL is running with `pg_isready` or `systemctl status postgresql`
- **Check connection:** Try connecting manually: `psql -h $DB_HOST -U $DB_USER -d $DB_NAME`

### Starting from scratch

To reset the database:

```bash
# Drop and recreate the database
dropdb -U your_postgres_user cex_exchange
createdb -U your_postgres_user cex_exchange

# Or with Docker Compose
docker compose down -v  # Removes volumes
docker compose up -d postgres
```

Then run migrations and seed again.

## Development

### Adding a new migration

```bash
# Create a new migration file
cd cex/packages/db
npx knex migrate:make migration_name --knexfile ./knexfile.cjs

# Edit the generated file in src/migrations/
# Then run migrations
pnpm migrate
```

### Adding a new seed

```bash
# Create a seed file
cd cex/packages/db/src/seeds
touch 00X_seed_name.cjs

# Follow the pattern in existing seed files
# Then run seed
pnpm seed
```

## Schema

The database schema includes:

- **users** - User accounts
- **assets** - Supported cryptocurrencies and tokens
- **networks** - Blockchain networks (Bitcoin, Ethereum, etc.)
- **asset_networks** - Asset-to-network mappings
- **wallets** - User wallets
- **orders** - Trading orders
- **trades** - Executed trades
- **deposits** - Deposit transactions
- **withdrawals** - Withdrawal transactions

See migration files in `src/migrations/` for detailed schema definitions.
