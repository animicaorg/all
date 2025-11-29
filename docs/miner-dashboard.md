# Miner Dashboard & Stratum API

This dashboard ships with a lightweight FastAPI metrics surface inside the Stratum pool (`animica.stratum_pool`). It surfaces pool status, worker stats, and recent blocks for the new `apps/miner-dashboard` React app.

## Running the Stratum backend with metrics

Profile-aware defaults live under `ops/profiles/`. Use `ops/run.sh` to wire the
selected profile into the pool:

```
# devnet (default)
ops/run.sh pool

# pick a profile explicitly
ops/run.sh --profile testnet pool
```

The HTTP API will be available at `http://<ANIMICA_POOL_API_BIND>` with:
- `GET /healthz` — health check
- `GET /api/pool/summary`
- `GET /api/miners`
- `GET /api/miners/{worker_id}`
- `GET /api/blocks/recent`

## Running the miner dashboard

Install dependencies and start the app through the orchestrator so the profile
base URL is exported automatically:

```
ops/run.sh dashboard
ops/run.sh --profile mainnet dashboard
```

The dashboard runs at http://localhost:5173 and reads `VITE_STRATUM_API_URL`
from the selected profile (default `http://127.0.0.1:8550`).

## Connecting a miner

Point your miner at the Stratum endpoint shown in the UI, for example:

```
miner --url stratum+tcp://localhost:3333 --worker rig-1 --address animica1...
```

Once shares are submitted, the worker appears in the **Miners** view with live hashrate and share stats.
