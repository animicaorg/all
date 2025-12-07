# Animica Devnet Quickstart

Follow these steps on a fresh Linux checkout to bring up the full devnet stack (nodes, miner, studio-services, explorer) with minimal setup.

## Prerequisites
- Docker and Docker Compose (v2+) installed and running
- Bash shell

## One-time setup
Install Node and Python dependencies and seed a writable devnet environment file:

```bash
./setup.sh
```

## Start the devnet
The following single command builds and launches the full devnet stack. It automatically ensures a default `tests/devnet/.env` exists and streams logs to `logs/spinup/spin_all.log`.

```bash
./ops/spinup/devnet.sh
```

After the services report healthy, access the stack at:
- RPC: http://localhost:8545
- Explorer: http://localhost:5173
- Studio Services: http://localhost:8787

Stop the stack with `docker compose -f tests/devnet/docker-compose.yml --profile dev down` when finished.

## Authentication and Billing

By default, the devnet runs in **free mode** with no authentication required and zero fees. All services are accessible without API keys.

### Enabling Paid Mode (Optional)

To test the monetization features locally:

1. Set environment variables before starting the devnet:

```bash
export ANIMICA_BILLING_MODE=paid
export ANIMICA_API_KEY_HEADER=x-api-key
export ANIMICA_DA_FEE_PER_BYTE=0.001
export ANIMICA_RPC_FEE_FLAT=0.01
export ANIMICA_AICF_BILLING_MODE=paid
export ANIMICA_AICF_RATE_PER_UNIT=0.5
```

2. Create a test API key file at `./data/valid_keys.json`:

```json
{
  "test-key-free": "free",
  "test-key-pro": "pro"
}
```

3. Start the devnet as usual. Now API calls require authentication:

```bash
# Without API key - returns 401 Unauthorized
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'

# With valid API key - succeeds
curl -X POST http://localhost:8545/rpc \
  -H "Content-Type: application/json" \
  -H "x-api-key: test-key-free" \
  -d '{"jsonrpc":"2.0","method":"chain.getHead","params":[],"id":1}'
```

For more details on monetization configuration, see [docs/monetization.md](monetization.md).
