# Miner Dashboard & Stratum API

This dashboard ships with a lightweight FastAPI metrics surface inside the Stratum pool (`animica.stratum_pool`). It surfaces pool status, worker stats, and recent blocks for the new `apps/miner-dashboard` React app.

## Running the Stratum backend with metrics

```
python -m animica.stratum_pool.cli \
  --rpc-url http://127.0.0.1:8545/rpc \
  --pool-address <your_pool_address> \
  --port 3333 \
  --api-port 8550
```

The HTTP API will be available on `http://localhost:8550` with:
- `GET /healthz` — health check
- `GET /api/pool/summary`
- `GET /api/miners`
- `GET /api/miners/{worker_id}`
- `GET /api/blocks/recent`

## Running the miner dashboard

Install dependencies and start the app:

```
pnpm install
pnpm --filter miner-dashboard dev
```

The dashboard runs at http://localhost:5173 and reads `VITE_STRATUM_API_URL` (defaults to `http://127.0.0.1:8550`).

## Connecting a miner

Point your miner at the Stratum endpoint shown in the UI, for example:

```
miner --url stratum+tcp://localhost:3333 --worker rig-1 --address animica1...
```

Once shares are submitted, the worker appears in the **Miners** view with live hashrate and share stats.
