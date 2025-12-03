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
