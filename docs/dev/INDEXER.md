# Indexer — Patterns & Example Schemas

This document explains **how to build a robust, reorg-safe indexer** for the
Animica stack: blocks, transactions, receipts/logs (events), PoIES proofs and
scores, DA blob commitments, randomness beacons, and AICF payouts. It includes:

- ingestion patterns (range backfill + live tails),
- reorg resilience and finality pointers,
- PostgreSQL-first schemas with **idempotent upserts**,
- queries for explorers/analytics (TPS, gas, miner share, AICF),
- optional derived tables (minute buckets, leaderboards),
- performance notes (partitioning, hot paths, JSONB vs columns).

> The node-side surfaces this indexer relies on are described in `spec/openrpc.json`
> and the DA/Randomness/AICF mounts. Names below match the RPC packages in `rpc/methods/*`
> and `da/randomness/aicf` adapters.

---

## 1) Architecture at a Glance

┌──────────────────────┐      ┌────────────────────────────┐
│  Node RPC + WS       │◀────▶│  Live Tail (WS newHeads)   │
│  /rpc  /ws           │      └───────────┬────────────────┘
│  DA /rand /aicf      │                  │
└─────────┬────────────┘                  ▼
│                    ┌────────────────────────────┐
│ Range Backfill     │  Ingestor (Workers)        │
└──────────────────▶ │  - fetch block by height   │
│  - decode CBOR tx/receipt  │
│  - verify chainId & links  │
│  - UPSERT rows (idempotent)│
└───────────┬────────────────┘
▼
┌────────────────────────────┐
│   PostgreSQL               │
│   - blocks/txs/receipts    │
│   - logs/events (topics)   │
│   - poies scores/proofs    │
│   - da blobs (commitments) │
│   - randomness beacons     │
│   - aicf payouts/jobs      │
└───────────┬────────────────┘
▼
┌────────────────────────────┐
│  Derived Views & APIs      │
│  - minute_tps/gas          │
│  - miner leaderboard       │
│  - token transfers index   │
│  - GraphQL/REST for UI     │
└────────────────────────────┘

**Key patterns**
- **Two loops**: (a) **Backfill** deterministic by height; (b) **Live tail**
  from WebSocket `newHeads` (and optionally `pendingTxs`).
- **Reorg-safe** via **canonical pointers**: we record which hash is canonical
  for each height; **on reorg**, rows from displaced blocks are marked
  `canonical=false` (soft-deleted) and foreign-keyed derived rows flip with the
  new canonical block.
- **Idempotency**: all upserts key on **content hashes**, not surrogate IDs.

---

## 2) RPC/WS Endpoints Used

- **Chain**
  - `chain.getHead()` → `{ number, hash }`
  - `chain.getBlockByNumber(number, includeTx=true)` → block + txs + (optional) receipts
  - `chain.getBlockByHash(hash, includeTx=true)`
  - `chain.getParams()` (for Γ/Θ and gas tables if you snapshot params)
- **Tx**
  - `tx.getTransactionByHash(hash)`
  - `tx.getTransactionReceipt(hash)`
- **State**
  - `state.getBalance(address)`, `state.getNonce(address)` (optional for derived)
- **WS**
  - subscribe `newHeads`, (`pendingTxs` optional)
- **DA**
  - `da/blob/{commitment}`, `da/proof?commitment=...` (if you index blob metadata)
- **Randomness**
  - `rand.getBeacon`, `rand.getHistory`
- **AICF**
  - `aicf.listProviders`, `aicf.getJob`, `aicf.claimPayout` (optional if you surface provider analytics)

---

## 3) PostgreSQL Example Schema (DDL)

> Target: **PostgreSQL 14+**. Use `uuid-ossp` if you want UUIDs for internal rows, but
> here we key on chain hashes (bytea) for idempotency. Use **partitions** for large tables.

```sql
-- Recommended extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- Domain types
CREATE DOMAIN hash32  AS bytea CHECK (octet_length(VALUE) = 32);  -- 0x… → raw bytes
CREATE DOMAIN addr    AS text  CHECK (char_length(VALUE) BETWEEN 20 AND 120); -- bech32m anim1…
CREATE DOMAIN u256    AS numeric(78,0);  -- up to 2^256-1

-- Canonical chain pointer per height (reorgs flip this)
CREATE TABLE canonical_chain (
  height      bigint PRIMARY KEY,
  block_hash  hash32 NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Blocks
CREATE TABLE blocks (
  hash          hash32 PRIMARY KEY,
  parent_hash   hash32 NOT NULL,
  height        bigint NOT NULL,
  timestamp     timestamptz NOT NULL,
  miner         addr,
  theta_micro   bigint,            -- Θ (micro-nats or encoded per spec)
  s_aggregate   numeric,           -- S = -ln(u) + Σψ
  psi_sum       numeric,           -- Σψ reported/derived
  gas_used      u256,
  gas_limit     u256,
  tx_count      integer NOT NULL,
  proofs_root   hash32,            -- if rooted in header
  receipts_root hash32,
  da_root       hash32,
  raw_header    jsonb NOT NULL,    -- verbatim header (canonical JSON or decoded CBOR)
  canonical     boolean NOT NULL DEFAULT false
);
CREATE INDEX ON blocks(height);
CREATE INDEX ON blocks(canonical) WHERE canonical = true;

-- Transactions
CREATE TABLE txs (
  hash          hash32 PRIMARY KEY,
  block_hash    hash32 REFERENCES blocks(hash) ON DELETE CASCADE,
  tx_index      integer NOT NULL,        -- position in block
  from_addr     addr NOT NULL,
  to_addr       addr,                    -- null for deploy/create
  kind          text NOT NULL,           -- transfer|deploy|call|blob
  value         u256 DEFAULT 0,          -- amount transferred
  gas_price     u256,
  gas_limit     u256,
  nonce         bigint,
  status        text,                    -- pending|success|revert|oog
  error_code    text,                    -- if any
  raw_tx        bytea NOT NULL,          -- raw CBOR tx
  sign_alg      text,                    -- dilithium3|sphincs_shake_128s
  chain_id      integer NOT NULL,
  canonical     boolean NOT NULL DEFAULT false,
  UNIQUE(block_hash, tx_index)
);
CREATE INDEX ON txs(block_hash);
CREATE INDEX ON txs(from_addr);
CREATE INDEX ON txs(to_addr);
CREATE INDEX ON txs(canonical) WHERE canonical = true;

-- Receipts (kept separate to allow re-fetch if blocks exclude receipts)
CREATE TABLE receipts (
  tx_hash       hash32 PRIMARY KEY REFERENCES txs(hash) ON DELETE CASCADE,
  status        text NOT NULL,    -- SUCCESS/REVERT/OOG
  gas_used      u256 NOT NULL,
  logs_bloom    bytea,
  raw_receipt   jsonb NOT NULL
);

-- Events / Logs: topics array + data blob
CREATE TABLE logs (
  id            bigserial PRIMARY KEY,
  tx_hash       hash32 NOT NULL REFERENCES txs(hash) ON DELETE CASCADE,
  block_hash    hash32 NOT NULL REFERENCES blocks(hash) ON DELETE CASCADE,
  log_index     integer NOT NULL, -- position within tx receipt
  address       addr NOT NULL,    -- contract address
  topics        bytea[] NOT NULL, -- array of 32-byte topics
  data          bytea,            -- arbitrary bytes
  canonical     boolean NOT NULL DEFAULT false,
  UNIQUE(tx_hash, log_index)
);
CREATE INDEX ON logs(address);
CREATE INDEX ON logs((topics[1])); -- first topic is the event signature
CREATE INDEX ON logs(canonical) WHERE canonical = true;

-- PoIES aggregates per block and per-proof-type (optional, fast analytics)
CREATE TABLE poies_block_scores (
  block_hash    hash32 PRIMARY KEY REFERENCES blocks(hash) ON DELETE CASCADE,
  s             numeric NOT NULL,
  theta         numeric NOT NULL,
  psi_total     numeric NOT NULL,
  psi_hash      numeric DEFAULT 0,
  psi_ai        numeric DEFAULT 0,
  psi_quantum   numeric DEFAULT 0,
  psi_storage   numeric DEFAULT 0,
  psi_vdf       numeric DEFAULT 0
);

-- Proof envelopes (optional; keep compact metadata only)
CREATE TABLE proofs (
  id            bigserial PRIMARY KEY,
  block_hash    hash32 NOT NULL REFERENCES blocks(hash) ON DELETE CASCADE,
  tx_hash       hash32,                 -- if attached to a tx (blob or call)
  type_id       smallint NOT NULL,      -- enum per consensus/types.py
  nullifier     hash32,                 -- anti-reuse
  psi_input     jsonb,                  -- metrics prior to caps
  accepted      boolean NOT NULL,       -- included & counted toward Σψ
  canonical     boolean NOT NULL DEFAULT false
);
CREATE INDEX ON proofs(block_hash);
CREATE INDEX ON proofs(type_id);
CREATE INDEX ON proofs(canonical) WHERE canonical = true;

-- DA Blobs (metadata only; actual blob stays in DA service)
CREATE TABLE da_blobs (
  commitment    hash32 PRIMARY KEY,     -- NMT root
  namespace     integer NOT NULL,
  size_bytes    bigint NOT NULL,
  block_hash    hash32 REFERENCES blocks(hash) ON DELETE SET NULL,
  poster        addr,
  receipt_json  jsonb,                  -- DA receipt
  available     boolean,
  canonical     boolean NOT NULL DEFAULT false
);
CREATE INDEX ON da_blobs(block_hash);

-- Randomness beacon
CREATE TABLE beacons (
  round_id      bigint PRIMARY KEY,
  block_hash    hash32,                 -- block that finalized this round (if applicable)
  vdf_output    hash32,
  vdf_proof     bytea,
  mix_hash      hash32,
  timestamp     timestamptz NOT NULL,
  canonical     boolean NOT NULL DEFAULT true
);

-- AICF payouts summary per block (optional)
CREATE TABLE aicf_payouts (
  id            bigserial PRIMARY KEY,
  block_hash    hash32 NOT NULL REFERENCES blocks(hash) ON DELETE CASCADE,
  provider_id   text NOT NULL,
  amount        u256 NOT NULL,
  kind          text NOT NULL,          -- AI|Quantum
  canonical     boolean NOT NULL DEFAULT false
);
CREATE INDEX ON aicf_payouts(provider_id);
CREATE INDEX ON aicf_payouts(canonical) WHERE canonical = true;

-- Ingest cursors (bookkeeping)
CREATE TABLE ingest_cursors (
  name          text PRIMARY KEY,       -- e.g., 'backfill', 'live'
  height        bigint NOT NULL,
  block_hash    hash32,                 -- last processed canonical hash
  updated_at    timestamptz NOT NULL DEFAULT now()
);

Partitioning (recommended for very large chains)

Partition blocks, txs, logs, proofs BY RANGE on height (blocks) and
via foreign key for dependent tables, or use time-based partitions by
month on timestamp. PostgreSQL declarative partitioning works well with
INSERT ... ON CONFLICT (upserts).

⸻

4) Ingestion Algorithms

4.1 Backfill (deterministic height scan)
	1.	Discover tip: head = chain.getHead().
	2.	Find cursor: read ingest_cursors['backfill'] (else start at 0/genesis).
	3.	For h in [cursor+1 .. head.number - FINALITY_LAG]:
	•	Fetch block = chain.getBlockByNumber(h, includeTx=true).
	•	Validate parent linkage (optional trust mode).
	•	UPSERT blocks (canonical=false initially).
	•	UPSERT txs + receipts + logs (canonical=false).
	•	Compute/insert poies_block_scores if exposed or derivable.
	•	If DA commitments visible, index da_blobs.
	•	Update canonical_chain(height=h, block_hash=block.hash).
	•	Mark canonical=true on blocks/txs/logs/proofs/da_blobs for this hash
and canonical=false on any rows at height h for other hashes (reorg fixup).
	•	Bump cursor.

FINALITY_LAG: Choose a safety window (e.g., 12–60 blocks) to defer marking
canonical during the live tail, depending on expected reorg depth.

4.2 Live Tail (WS newHeads)
	•	Subscribe newHeads. On each head:
	•	Fetch full block by hash (or by number).
	•	Apply the same upsert sequence as backfill.
	•	Update canonical_chain and flip canonical flags at this height.
	•	For pendingTxs (optional):
	•	Maintain an in-memory cache or a mempool table with TTL (not persisted across restarts).
	•	When a pending tx appears in a canonical block, delete its mempool row.

4.3 Reorg Handling

When a different block becomes canonical at height h:
	•	UPDATE blocks SET canonical=false WHERE height=h AND hash<>new_hash;
	•	Flip canonical=true on the new block.
	•	For dependent tables:
	•	UPDATE txs/logs/proofs SET canonical=false WHERE block_hash IN (old_hashes_at_h);
	•	UPDATE txs/logs/proofs SET canonical=true  WHERE block_hash=new_hash;
	•	Optional garbage collection:
	•	Keep orphan data for N days (useful for debugging) then purge.

Keep unique constraints scoped by (block_hash, index) to allow storage
of both the displaced and canonical block at the same height.

⸻

5) CBOR & Domains
	•	Decode transactions using the canonical CBOR from spec/tx_format.cddl.
	•	Do not re-serialize for storage when “verbatim” is required—store raw CBOR
alongside normalized fields to avoid format drift.
	•	Use sign_alg and chain_id from the envelope for display & filters.

⸻

6) Derived Tables & Materializations

Minute-level TPS & Gas

CREATE MATERIALIZED VIEW mv_minute_chain_stats AS
SELECT date_trunc('minute', b.timestamp) AS minute,
       count(t.hash)                     AS txs,
       sum(t.gas_limit)                  AS gas_limit_sum,
       sum(r.gas_used)                   AS gas_used_sum
FROM blocks b
JOIN txs t ON t.block_hash = b.hash AND t.canonical
LEFT JOIN receipts r ON r.tx_hash = t.hash
WHERE b.canonical
GROUP BY 1
WITH NO DATA;

-- Refresh job (cron):
-- REFRESH MATERIALIZED VIEW CONCURRENTLY mv_minute_chain_stats;

Miner/Producer Leaderboard (by Σψ or blocks)

SELECT miner,
       count(*) AS blocks,
       sum(p.psi_total) AS psi_total
FROM blocks b
LEFT JOIN poies_block_scores p ON p.block_hash = b.hash
WHERE b.canonical
GROUP BY miner
ORDER BY psi_total DESC NULLS LAST
LIMIT 100;

Event Index (by first topic)

-- Find all Transfer events of a canonical Animica token ABI:
SELECT l.block_hash, l.tx_hash, l.log_index, l.address, l.data
FROM logs l
WHERE l.canonical
  AND l.topics[1] = decode('ab…cd', 'hex')  -- event signature topic0
ORDER BY l.block_hash, l.log_index
LIMIT 100;


⸻

7) Example Ingestor (Python, async)

Pseudocode focusing on correctness & idempotency.

import asyncio, json, aiohttp, asyncpg

RPC_URL = "https://rpc.dev.animica.xyz"

async def rpc_call(session, method, params=None):
  body = {"jsonrpc":"2.0","id":1,"method":method,"params":params or []}
  async with session.post(RPC_URL + "/rpc", json=body, timeout=20) as r:
    j = await r.json()
    if "error" in j: raise RuntimeError(j["error"])
    return j["result"]

async def upsert_block(conn, block):
  # Example: write blocks + txs in a transaction
  async with conn.transaction():
    await conn.execute("""
      INSERT INTO blocks(hash,parent_hash,height,timestamp,miner,theta_micro,s_aggregate,psi_sum,
                         gas_used,gas_limit,tx_count,proofs_root,receipts_root,da_root,raw_header,canonical)
      VALUES($1,$2,$3,to_timestamp($4),$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,false)
      ON CONFLICT (hash) DO NOTHING
    """,
      bytes.fromhex(block["hash"][2:]),
      bytes.fromhex(block["parentHash"][2:]),
      block["number"], block["timestamp"],
      block.get("miner"), block.get("thetaMicro"), block.get("s"),
      block.get("psiSum"), block.get("gasUsed"), block.get("gasLimit"),
      len(block.get("transactions", [])),
      maybe_hex(block.get("proofsRoot")), maybe_hex(block.get("receiptsRoot")),
      maybe_hex(block.get("daRoot")), json.dumps(block["header"])
    )
    # txs...
    for i, tx in enumerate(block["transactions"]):
      await conn.execute("""
        INSERT INTO txs(hash,block_hash,tx_index,from_addr,to_addr,kind,value,gas_price,gas_limit,nonce,
                        status,error_code,raw_tx,sign_alg,chain_id,canonical)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,decode($13,'hex'),$14,$15,false)
        ON CONFLICT (hash) DO NOTHING
      """,
        hx(tx["hash"]), hx(block["hash"]), i, tx["from"], tx.get("to"),
        tx["kind"], tx.get("value",0), tx.get("gasPrice",0), tx.get("gasLimit",0),
        tx.get("nonce"), tx.get("status"), tx.get("errorCode"),
        tx["rawCborHex"][2:], tx.get("signAlg"), tx["chainId"]
      )
    # mark canonical pointer
    await conn.execute("""
      INSERT INTO canonical_chain(height, block_hash)
      VALUES($1, $2) ON CONFLICT (height) DO UPDATE SET block_hash=EXCLUDED.block_hash, updated_at=now()
    """, block["number"], hx(block["hash"]))

    # flip canonical flags at this height
    await conn.execute("""
      WITH target AS (SELECT $1::bytea AS bh, $2::bigint AS h)
      UPDATE blocks b SET canonical = (b.hash = (SELECT bh FROM target))
      WHERE b.height = (SELECT h FROM target);
    """, hx(block["hash"]), block["number"])
    await conn.execute("""
      UPDATE txs t SET canonical=true WHERE t.block_hash=$1;
      UPDATE txs t SET canonical=false WHERE t.block_hash IN (
        SELECT hash FROM blocks WHERE height=$2 AND hash<>$1
      );
    """, hx(block["hash"]), block["number"])

def hx(h): return bytes.fromhex(h[2:]) if h else None
def maybe_hex(h): return h if h is None else bytes.fromhex(h[2:])

async def backfill(pool):
  async with aiohttp.ClientSession() as s, pool.acquire() as conn:
    head = await rpc_call(s, "chain.getHead")
    cur = await conn.fetchrow("SELECT height FROM ingest_cursors WHERE name='backfill'")
    start = (cur["height"] if cur else -1) + 1
    tip   = head["number"] - 24  # finality lag
    for h in range(start, max(tip+1, start)):
      blk = await rpc_call(s, "chain.getBlockByNumber", [h, True])
      await upsert_block(conn, blk)
      await conn.execute("""
        INSERT INTO ingest_cursors(name,height,block_hash)
        VALUES('backfill',$1,$2)
        ON CONFLICT (name) DO UPDATE SET height=EXCLUDED.height, block_hash=EXCLUDED.block_hash, updated_at=now()
      """, h, hx(blk["hash"]))

async def main():
  pool = await asyncpg.create_pool(dsn="postgres://user:pass@localhost/animica")
  await backfill(pool)
  # live tail omitted for brevity: subscribe /ws newHeads, call upsert_block on each
  await pool.close()

if __name__ == "__main__":
  asyncio.run(main())


⸻

8) Performance & Storage Notes
	•	Hot read paths: blocks(height DESC), latest txs by address (from/to),
logs by topics[1] and address.
	•	Compression: TOAST for jsonb and bytea keeps storage reasonable.
	•	JSONB vs Columns: Put frequently-filtered fields in columns; keep full
raw payloads as jsonb/bytea for fidelity.
	•	Vacuum & autovacuum: tune for heavy upsert workloads, especially when flipping canonical.
	•	Partitioning: monthly partitions on timestamp or 10M-block ranges help maintenance.
	•	Connection pooling: use pgbouncer in transaction mode.

⸻

9) Quality & Correctness
	•	Idempotency: ensure every write is INSERT ... ON CONFLICT DO UPDATE/NOTHING
keyed by content hash.
	•	Re-verification (optional):
	•	PQ signatures: spot-verify a sample if you don’t fully trust ingress.
	•	DA proofs: verify receipts/NMT proofs on demand rather than eagerly.
	•	PoIES inputs: if exposed, re-check summations (Σψ) for analytics integrity.
	•	Monitoring:
	•	Liveness probe (last cursor age),
	•	Gap detector (missed heights),
	•	Reorg counter and maximum depth observed,
	•	Lag from head (in blocks & time).

⸻

10) Common Queries (Examples)

Latest head + TPS (1m window)

SELECT (SELECT height FROM blocks WHERE canonical ORDER BY height DESC LIMIT 1) AS head,
       (SELECT txs FROM mv_minute_chain_stats ORDER BY minute DESC LIMIT 1)    AS txs_last_minute;

Address activity

SELECT t.hash, t.block_hash, t.tx_index, t.kind, t.value, r.status, r.gas_used
FROM txs t
LEFT JOIN receipts r ON r.tx_hash = t.hash
WHERE t.canonical AND (t.from_addr = $1 OR t.to_addr = $1)
ORDER BY t.block_hash DESC, t.tx_index DESC
LIMIT 100;

Event search (topic0 + contract)

SELECT l.block_hash, l.tx_hash, l.log_index, l.data
FROM logs l
WHERE l.canonical
  AND l.address = $1
  AND l.topics[1] = $2::bytea
ORDER BY l.block_hash DESC, l.log_index DESC
LIMIT 200;

AICF provider payouts (30d)

SELECT date_trunc('day', b.timestamp) AS day, sum(p.amount) AS amount
FROM aicf_payouts p
JOIN blocks b ON b.hash = p.block_hash
WHERE p.canonical AND p.provider_id = $1 AND b.timestamp > now() - interval '30 days'
GROUP BY 1 ORDER BY 1;


⸻

11) Testing & Reproducibility
	•	Seed a devnet using docs/dev/QUICKSTART.md.
	•	Run the indexer against it; assert:
	•	Zero gaps in canonical_chain coverage,
	•	parent_hash chains from genesis,
	•	Reorg simulation test: import two forks, ensure canonical flips cleanly,
	•	Deterministic hashes for CBOR-stored txs across re-ingestions.

⸻

12) Versioning & Migrations
	•	Use simple, append-only migrations with a tool like sqitch or golang-migrate.
	•	Keep a schema_version table; bump on breaking changes.
	•	For large changes (e.g., new PoIES dimensions), create new columns then backfill async.

⸻

13) Optional GraphQL Facade

Many explorers benefit from GraphQL (typed filtering & pagination). Map tables
to types:

type Block { hash: ID!, height: Int!, timestamp: Time!, txCount: Int!, gasUsed: BigInt, miner: String }
type Tx    { hash: ID!, from: String!, to: String, kind: String!, value: BigInt, status: String, gasUsed: BigInt }
type Log   { txHash: ID!, index: Int!, address: String!, topics: [Bytes32!]!, data: Bytes }

Implement resolvers with SQL that always filter canonical=true.

⸻

14) Security Considerations
	•	Treat RPC inputs as untrusted; validate hex/lengths when decoding.
	•	Apply rate limits to your public API. Separate ingestion DB from read replicas.
	•	If you expose blob get/proof proxies, guard with size/rate caps.

⸻

Appendix A — Minimal Token Transfer View

For popular “Transfer(address,address,uint256)” events:

CREATE VIEW token_transfers AS
SELECT l.block_hash, l.tx_hash, l.log_index, l.address AS token,
       encode(l.topics[2], 'hex') AS from_topic,  -- parse to address in app layer
       encode(l.topics[3], 'hex') AS to_topic,
       (get_uint256_from_bytes(l.data))::numeric(78,0) AS amount
FROM logs l
WHERE l.canonical AND l.topics[1] = decode('<topic0_of_Transfer>', 'hex');

(Implement get_uint256_from_bytes(bytea) as a SQL function or parse in API.)

⸻

That’s it. With these tables and patterns you can power an explorer,
analytics dashboards, and developer-facing endpoints with predictable performance
and safe behavior under reorgs.

