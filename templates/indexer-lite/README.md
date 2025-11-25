# Indexer Lite — Template

A batteries-included scaffold for a **lightweight, production-friendly Animica indexer**. It ingests canonical blocks, transactions, receipts, and (optionally) contract events from a node via HTTP JSON-RPC and WebSocket subscriptions, persists them into a SQL database (SQLite or Postgres), and exposes an optional **read-only API** and **Prometheus metrics**.

This template is designed to be:
- **Small & understandable** — a clear reference you can extend.
- **Safe by default** — idempotent writes, gentle reorg handling, bounded concurrency.
- **Ops friendly** — `.env` config, structured logs, health endpoints, metrics.
- **Portable** — runs on SQLite for dev, Postgres for prod, with a simple process model.

---

## What gets generated

After rendering the template you’ll get a project like:

{{project_slug}}/
.env.example
pyproject.toml
README.md
src/
{{module_name}}/
init.py
config.py           # loads env / CLI args
logging.py          # structured logging setup
rpc.py              # HTTP JSON-RPC client (retry/backoff, timeouts)
ws.py               # WS subscriber for newHeads stream (auto-reconnect)
models.py           # SQLAlchemy models (Blocks, Txs, Receipts, Logs)
storage.py          # engine/session factories, migrations helper
decode.py           # helpers to normalize RPC payloads
indexer.py          # orchestrator: backfill + live tail + reorg handling
api.py              # optional FastAPI read endpoints (if enabled)
metrics.py          # Prometheus counters/histograms
cli.py              # typer/argparse entrypoint (backfill, tail, api, all)
scripts/
run_all.sh            # example supervisor script (dev)
tests/
test_smoke.py         # basic connectivity + first block ingest

> The generated code prefers **Python 3.11+** and **SQLAlchemy 2.x**. HTTP is done with `httpx`, WS with `websockets` (or `httpx[http2]` + `websocket` adapter), CLI via `typer`, API via `FastAPI`, and metrics via `prometheus_client`.

---

## Quickstart

### 1) Render the template

Use the shared templates engine (already in this repo):

```bash
python -m templates.engine.cli render \
  templates/indexer-lite \
  --out ./{{project_slug}} \
  --vars templates/indexer-lite/variables.json

You’ll be prompted (or pass --set key=value) for:
	•	project_name, project_slug, module_name
	•	rpc_url, ws_url, chain_id
	•	db_engine, db_url
	•	start_height
	•	include_api, api_port, metrics_port
	•	log_level, fetch_concurrency, backfill_batch_size, …

2) Configure

Copy .env.example to .env and tweak:

RPC_URL={{rpc_url}}
WS_URL={{ws_url}}
CHAIN_ID={{chain_id}}

DB_URL={{db_url}}

START_HEIGHT={{start_height}}
FETCH_CONCURRENCY={{fetch_concurrency}}
BACKFILL_BATCH_SIZE={{backfill_batch_size}}

INCLUDE_API={{include_api}}
API_PORT={{api_port}}
METRICS_PORT={{metrics_port}}

LOG_LEVEL={{log_level}}
HTTP_TIMEOUT_SEC={{http_timeout_sec}}
WS_MAX_RETRIES={{ws_max_retries}}
DB_POOL_SIZE={{db_pool_size}}

3) Install & run

cd {{project_slug}}
python -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install -e .

Run a historical backfill from the configured START_HEIGHT:

python -m {{module_name}}.cli backfill

Start live tail (WS newHeads + HTTP block fetch):

python -m {{module_name}}.cli tail

Or do both (in one process):

python -m {{module_name}}.cli all

If you enabled the API:

python -m {{module_name}}.cli api
# GET http://localhost:{{api_port}}/healthz
# GET http://localhost:{{api_port}}/blocks/1234

Prometheus metrics are exposed at http://localhost:{{metrics_port}}/metrics.

⸻

Architecture

Data flow (happy path)

        +-----------------+        +---------------------+        +------------------+
        |   WebSocket     |        |   HTTP JSON-RPC     |        |      Storage     |
        |  /ws newHeads   |        |  /rpc getBlockBy... |        |  (SQLAlchemy)    |
        +--------+--------+        +----------+----------+        +---------+--------+
                 |                            |                            |
                 | (1) Header tip stream      |                            |
                 v                            |                            |
            [ws.py]                           |                            |
                 |                            |                            |
                 | (2) For each header, fetch full block (txs, receipts)   |
                 +---------------------------> v                            |
                                          [rpc.py]                          |
                                                 (3) Normalize & decode     |
                                                     to internal structs    |
                                                     via [decode.py]        |
                                                                            |
                                                              (4) Upsert    |
                                                              block/tx/logs |
                                                              with reorg    |
                                                              safety        v
                                                                    [storage.py]

Reorg handling
	•	Idempotent upserts keyed by (block_hash) and (tx_hash).
	•	Chain continuity tracked by parent_hash. On tip switch:
	1.	Find common ancestor by walking parents in DB.
	2.	Prune rows at heights > ancestor_height for the old branch.
	3.	Insert the new branch blocks/txs/logs.
	•	Depth and frequency of reorgs are counted and exposed via metrics.

Bounded concurrency
	•	Backfill uses a height-ranged work queue with BACKFILL_BATCH_SIZE and FETCH_CONCURRENCY.
	•	Live tail processes one head at a time, but within a head can parallelize ancillary RPCs (e.g., receipt fetch if not inlined).

⸻

RPC methods used

The indexer targets the minimal OpenRPC surface shipped by the node:
	•	chain.getHead → tip number/hash
	•	chain.getBlockByNumber(number, includeTxs=true, includeReceipts=true)
or chain.getBlockByHash(hash, includeTxs=true, includeReceipts=true)
	•	(Optional) tx.getTransactionReceipt(txHash) if receipts aren’t embedded

WS topic:
	•	newHeads (headers only) → then resolve to full block via HTTP.

Names match spec/openrpc.json and the rpc/ package in this repo.

⸻

Database model (default)

Tables (SQLAlchemy models defined in models.py):
	•	blocks
	•	height (int, indexed), hash (pk), parent_hash, timestamp, tx_count, gas_used, gas_limit, state_root, receipts_root, proposer, da_root
	•	txs
	•	hash (pk), block_hash (fk), index (int), from_addr, to_addr, nonce, value, gas_price, gas_limit, status, input (hex/bytes), type
	•	receipts
	•	tx_hash (pk, fk), status, gas_used, logs_bloom, contract_address (nullable)
	•	logs (events)
	•	id (pk), tx_hash (fk), block_hash (fk), index (int), address, topic0..topic3, data (bytes)

Indices:
	•	blocks(height), txs(block_hash, index), txs(from_addr), txs(to_addr), logs(address), logs(topic0), plus composite indexes you can enable per workload.

SQLite uses WITHOUT ROWID where beneficial; Postgres migrations add conditional indexes.

⸻

Optional read API

If INCLUDE_API=true, the project serves FastAPI routes:
	•	GET /healthz, GET /readyz
	•	GET /blocks/{height} → canonical block + tx summary
	•	GET /tx/{txHash} → tx + receipt + logs
	•	GET /logs?address=…&topic0=…&from=…&to=…&limit=… → filterable event query
	•	GET /head → current known tip (from DB)
	•	GET /metrics → Prometheus scrape endpoint

Swagger UI is disabled in prod, but you can enable it in dev.

⸻

Prometheus metrics

Exposed via /metrics:
	•	Counters
	•	indexer_blocks_ingested_total{mode="backfill|tail"}
	•	indexer_txs_ingested_total
	•	indexer_logs_ingested_total
	•	indexer_reorgs_total
	•	indexer_ws_reconnects_total
	•	indexer_rpc_errors_total{method=...}
	•	Histograms/Summaries
	•	indexer_rpc_latency_seconds{method=...}
	•	indexer_block_write_seconds
	•	indexer_backfill_batch_seconds
	•	Gauges
	•	indexer_tip_height
	•	indexer_backfill_lag_blocks

⸻

Configuration reference

Key settings (merged from .env and CLI flags):

Name	Type	Purpose
RPC_URL	str	HTTP JSON-RPC base URL (e.g., http://localhost:8545/rpc)
WS_URL	str	WebSocket URL for newHeads (e.g., ws://localhost:8545/ws)
CHAIN_ID	int	CAIP-2 chain id (1 mainnet, 2 testnet, 1337 devnet)
DB_URL	str	SQLAlchemy URL (sqlite:///./indexer.db, or Postgres)
START_HEIGHT	int	Backfill starting block (0 = genesis)
FETCH_CONCURRENCY	int	Max in-flight RPC tasks
BACKFILL_BATCH_SIZE	int	Blocks per backfill batch
HTTP_TIMEOUT_SEC	int	Per-RPC timeout
WS_MAX_RETRIES	int	0 = infinite, else max reconnect attempts
INCLUDE_API	bool	Serve FastAPI read endpoints
API_PORT	int	API port
METRICS_PORT	int	Prometheus port
LOG_LEVEL	enum	`debug
DB_POOL_SIZE	int	Postgres pool size


⸻

CLI

The generated cli.py exposes:

Usage: python -m {{module_name}}.cli [COMMAND] [OPTIONS]

Commands:
  backfill   Ingest historical range from START_HEIGHT to current head.
  tail       Subscribe to new heads and index forward.
  all        Run backfill (until caught up) then continue tail.
  api        Start read-only API and metrics server.

Examples:

# Backfill first, then follow tip
python -m {{module_name}}.cli all --start {{start_height}} --concurrency {{fetch_concurrency}}

# Live tail only (no backfill)
python -m {{module_name}}.cli tail

# API on custom port
python -m {{module_name}}.cli api --port 8088


⸻

Reorgs — how they are handled

On receipt of a header whose parent_hash is unknown or whose height collides with an existing canonical block:
	1.	Walk parents (via RPC if needed) until reaching a block we have.
	2.	Compute common ancestor.
	3.	Delete rows for old branch heights > ancestor_height (blocks/txs/logs/receipts in one transaction).
	4.	Insert the new branch from ancestor+1 to tip.

All writes are idempotent upserts by (block_hash) and (tx_hash).
Depths are recorded in indexer_reorgs_total with a depth label (bucketized).

⸻

Filters & extensions

The scaffold includes hooks you can extend:
	•	Event filters: preload a set of (address, topic0) pairs to persist only what you need.
	•	Derived tables: e.g., track balances or custom protocol state by reacting to events.
	•	DA / AICF: add tables for DA commitments or AICF job events if your app needs them.

⸻

Performance tips
	•	For backfill, increase BACKFILL_BATCH_SIZE and FETCH_CONCURRENCY gradually while watching:
	•	RPC latency/error rates
	•	DB write times
	•	Node CPU and rate limits (use the node’s /metrics)
	•	Use SQLite with WAL for dev; for prod, use Postgres with reasonable DB_POOL_SIZE.
	•	Place DB and indexer on the same AZ/region as the node to minimize RTT.

⸻

Deployment notes

Systemd (single host)

Create a unit file:

[Unit]
Description=Animica Indexer Lite
After=network-online.target

[Service]
WorkingDirectory=/opt/{{project_slug}}
EnvironmentFile=/opt/{{project_slug}}/.env
ExecStart=/opt/{{project_slug}}/.venv/bin/python -m {{module_name}}.cli all
Restart=always
RestartSec=3
User=indexer

[Install]
WantedBy=multi-user.target

Docker

A minimal Dockerfile (example):

FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -U pip && pip install .
ENV PYTHONUNBUFFERED=1
CMD ["python","-m","{{module_name}}.cli","all"]

Expose ${METRICS_PORT} for Prometheus; mount .env or pass -e vars.

⸻

Example: simple query (Python)

# run this inside the rendered project venv
from sqlalchemy import select
from {{module_name}}.storage import SessionLocal
from {{module_name}}.models import Block, Tx

with SessionLocal() as s:
    head = s.execute(select(Block).order_by(Block.height.desc()).limit(1)).scalar_one()
    print("Tip:", head.height, head.hash)
    txs = s.execute(select(Tx).where(Tx.block_hash == head.hash).order_by(Tx.index)).scalars().all()
    for tx in txs[:5]:
        print(tx.index, tx.hash, tx.from_addr, "→", tx.to_addr)


⸻

Testing
	•	tests/test_smoke.py verifies:
	•	RPC reachability
	•	DB connection/migration
	•	Ingest of at least one block
	•	You can add property tests for idempotence and reorg recovery using ephemeral DBs.

⸻

Troubleshooting
	•	Backfill seems slow → lower BACKFILL_BATCH_SIZE, confirm node allows batching of block fetches, check DB I/O.
	•	WS disconnects often → set WS_MAX_RETRIES=0 (infinite) and verify ingress/proxies don’t drop idle connections.
	•	Reorg storms → verify node peers/health; consider pausing tail until stabilization, then backfill gap.

⸻

Roadmap ideas
	•	Optional partitions for large logs tables (Postgres).
	•	Bloom filters or topic indexes per contract domain.
	•	Pluggable sinks (Kafka) for stream processing.
	•	Materialized views for common dashboards.

⸻

License

Generated projects inherit the repository’s license unless you override it in your template.

⸻

Happy indexing!
