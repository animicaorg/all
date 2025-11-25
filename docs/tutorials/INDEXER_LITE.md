# Indexer Lite — Ingest Blocks and Plot Γ (Gamma) Utilization

This tutorial walks you through building a **minimal, production-friendly indexer** that:
- Ingests blocks from a running Animica node via JSON-RPC,
- Extracts PoIES metrics (Θ, per-proof ψ, and Σψ),
- Computes **Γ (Gamma) utilization** (how much proof “budget” is being exercised),
- Serves a tiny JSON API and renders **Γ charts** in a simple dashboard.

It’s intentionally lightweight: **SQLite**, a single **Python** process, and a static **Chart.js** page.

> **Definitions (PoIES recap)**
> - **ψ** (psi): contribution from a proof (or proof type) to block acceptance weight.
> - **Θ** (theta): moving difficulty/threshold.
> - **Γ** (gamma): policy “budget” cap across proof types (and/or total). We’ll visualize how Σψ (and per-type ψ) track against Γ caps.

---

## 0) Prerequisites

- A running Animica node (local devnet is fine) with RPC enabled.
- Python 3.10+ with `omni_sdk` installed (from `sdk/python`), plus `sqlite3`, `fastapi`, `uvicorn`, and `msgspec`.
- The node’s RPC URL (e.g. `http://127.0.0.1:8545`) and chainId.

---

## 1) Schema: Minimal SQLite

Create a small DB to store block headers and PoIES metrics:

```sql
-- schema.sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blocks (
  height      INTEGER PRIMARY KEY,
  hash        TEXT NOT NULL,
  parent_hash TEXT NOT NULL,
  timestamp   INTEGER NOT NULL,
  theta       REAL    NOT NULL, -- Θ from header
  psi_total   REAL    NOT NULL, -- Σψ (sum over proof kinds)
  gamma_util  REAL    NOT NULL, -- computed utilization in [0, 1]
  UNIQUE(height)
);

-- Per-kind ψ breakdown (e.g., hash, ai, quantum, storage, vdf)
CREATE TABLE IF NOT EXISTS psi_by_kind (
  height INTEGER NOT NULL,
  kind   TEXT    NOT NULL,
  psi    REAL    NOT NULL,
  PRIMARY KEY (height, kind),
  FOREIGN KEY (height) REFERENCES blocks(height) ON DELETE CASCADE
);

-- Keep the last fully indexed height
INSERT OR IGNORE INTO meta(k, v) VALUES ('last_height', '-1');

You can also add indexes like CREATE INDEX idx_blocks_time ON blocks(timestamp); for chart ranges.

⸻

2) Where the Numbers Come From
	•	chain.getParams → includes PoIES policy parameters (including Γ caps).
Example fields you’ll use:
	•	poiesPolicy.totalGammaCap (scalar budget per block),
	•	poiesPolicy.perTypeCaps (map: kind → cap).
	•	chain.getHead → returns the latest head (height/hash).
	•	chain.getBlockByNumber (or chain.getBlockByHash) → returns:
	•	Header fields (e.g., theta, timestamp, hash, parentHash),
	•	Proofs/receipts or a per-type ψ breakdown (depending on your node’s RPC config).
For Indexer-Lite, we assume the block view exposes poies.psiByKind and poies.psiTotal. If not, you can compute Σψ by summing receipt metrics or proof envelopes as exposed by your RPC (adjust the code comments accordingly).

Field names can vary slightly by build; skim your node’s OpenRPC (/openrpc.json) or rpc/models.py to confirm.

⸻

3) Ingestor (Python)

A robust-but-small ingestor that:
	•	Loads policy Γ caps once (cache),
	•	Backfills from last_height+1 to head,
	•	Detects reorgs (parent mismatch) and rolls back,
	•	Computes gamma_util = min( Σ_k min(ψ_k, Γ_k), Γ_total ) / Γ_total.

# ingest.py
import sqlite3, time, math, json
from typing import Dict, Any, Tuple

from omni_sdk.config import Config
from omni_sdk.rpc.http import HttpRpc

RPC_URL  = "http://127.0.0.1:8545"
CHAIN_ID = 1
POLL_SEC = 2.0

cfg = Config(rpc_url=RPC_URL, chain_id=CHAIN_ID)
rpc = HttpRpc(cfg)

DB = "indexer.sqlite"

def db_conn():
    return sqlite3.connect(DB)

def get_last_height(cur) -> int:
    row = cur.execute("SELECT v FROM meta WHERE k='last_height'").fetchone()
    return int(row[0]) if row else -1

def set_last_height(cur, h: int):
    cur.execute("UPDATE meta SET v=? WHERE k='last_height'", (str(h),))

def load_gamma_policy(rpc: HttpRpc) -> Tuple[float, Dict[str, float]]:
    """
    Fetch PoIES policy caps from chain params.
    Returns: (Gamma_total_cap, per_type_caps)
    """
    params = rpc.call("chain.getParams", {})
    policy = params.get("poiesPolicy", {})  # adjust if your node nests differently
    total_cap = float(policy.get("totalGammaCap", 1.0))
    per_type  = {k: float(v) for k, v in policy.get("perTypeCaps", {}).items()}
    if total_cap <= 0:
        total_cap = 1.0
    return total_cap, per_type

def gamma_utilization(psi_by_kind: Dict[str, float], gamma_total: float, gamma_per_type: Dict[str, float]) -> float:
    """
    Γ utilization = min( sum_k min(ψ_k, Γ_k), Γ_total ) / Γ_total.
    """
    clipped = 0.0
    for k, psi in psi_by_kind.items():
        cap = gamma_per_type.get(k, float("inf"))
        clipped += min(float(psi), cap)
    clipped = min(clipped, gamma_total)
    return clipped / gamma_total if gamma_total > 0 else 0.0

def fetch_head() -> Dict[str, Any]:
    return rpc.call("chain.getHead", {})

def fetch_block_by_number(h: int) -> Dict[str, Any]:
    # If your RPC needs flags like "includeProofs": True, add them here.
    return rpc.call("chain.getBlockByNumber", {"number": h})

def insert_block(cur, blk: Dict[str, Any], gamma_total: float, gamma_per_type: Dict[str, float]):
    header = blk["header"]
    h   = int(header["height"])
    ts  = int(header.get("timestamp", header.get("time", 0)))
    bh  = header["hash"]
    ph  = header["parentHash"]
    th  = float(header.get("theta", 0.0))

    # Prefer direct fields if present; otherwise derive Σψ and per-kind from proofs/receipts.
    poies = blk.get("poies", {})
    psi_total = float(poies.get("psiTotal", 0.0))
    psi_map   = {k: float(v) for k, v in poies.get("psiByKind", {}).items()}

    gutil = gamma_utilization(psi_map, gamma_total, gamma_per_type)

    cur.execute("""
      INSERT OR REPLACE INTO blocks(height, hash, parent_hash, timestamp, theta, psi_total, gamma_util)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (h, bh, ph, ts, th, psi_total, gutil))

    for kind, psi in psi_map.items():
        cur.execute("""
          INSERT OR REPLACE INTO psi_by_kind(height, kind, psi)
          VALUES (?, ?, ?)
        """, (h, kind, float(psi)))

def tip_hash(cur, h: int) -> str | None:
    row = cur.execute("SELECT hash FROM blocks WHERE height=?", (h,)).fetchone()
    return row[0] if row else None

def rollback_to(cur, target_h: int):
    cur.execute("DELETE FROM psi_by_kind WHERE height > ?", (target_h,))
    cur.execute("DELETE FROM blocks WHERE height > ?", (target_h,))
    set_last_height(cur, target_h)

def main():
    gamma_total, gamma_per_type = load_gamma_policy(rpc)
    print("Γ_total:", gamma_total, "Γ_per_type:", gamma_per_type)

    with db_conn() as con:
        cur = con.cursor()
        # Ensure schema exists (idempotent)
        cur.executescript(open("schema.sql", "r", encoding="utf-8").read())
        con.commit()

    while True:
        try:
            head = fetch_head()
            head_h = int(head["height"])
            with db_conn() as con:
                cur = con.cursor()
                last = get_last_height(cur)

                # Backfill
                target = head_h
                i = last + 1
                while i <= target:
                    blk = fetch_block_by_number(i)
                    header = blk["header"]
                    ph = header["parentHash"]
                    # Reorg check
                    if i > 0:
                        prev_hash = tip_hash(cur, i-1)
                        if prev_hash and prev_hash != ph:
                            # Reorg detected: roll back to parent
                            print(f"[reorg] at {i}, rolling back...")
                            rollback_to(cur, i-1)
                            con.commit()
                            # After rollback, restart this height with new parent linkage
                            continue

                    insert_block(cur, blk, gamma_total, gamma_per_type)
                    set_last_height(cur, i)
                    con.commit()
                    if i % 100 == 0:
                        print(f"indexed up to {i}")
                    i += 1

        except Exception as e:
            print("ingest error:", e)

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()

If your block view doesn’t expose poies.psiByKind/psiTotal, replace that section with logic that aggregates ψ from proof receipts in the block (available in your RPC if enabled).

⸻

4) Tiny JSON API (FastAPI)

Expose time-series for your dashboard:

# api.py
import sqlite3, json
from fastapi import FastAPI
from fastapi.responses import JSONResponse

DB = "indexer.sqlite"
app = FastAPI(title="Animica Indexer Lite")

def q(sql, args=()):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    with con:
        cur = con.cursor()
        rows = cur.execute(sql, args).fetchall()
    return [dict(r) for r in rows]

@app.get("/api/series/gamma")
def series_gamma(limit: int = 7200):
    rows = q("""
      SELECT height, timestamp, gamma_util, psi_total, theta
      FROM blocks ORDER BY height DESC LIMIT ?
    """, (limit,))
    rows.reverse()
    return JSONResponse(rows)

@app.get("/api/series/psi_by_kind")
def series_psi_by_kind(kind: str, limit: int = 7200):
    rows = q("""
      SELECT b.height, b.timestamp, p.psi
      FROM psi_by_kind p
      JOIN blocks b ON b.height = p.height
      WHERE p.kind = ?
      ORDER BY b.height DESC LIMIT ?
    """, (kind, limit))
    rows.reverse()
    return JSONResponse(rows)

@app.get("/api/healthz")
def healthz():
    return {"ok": True}

Run it:

uvicorn api:app --reload --port 8080


⸻

5) Dashboard (Static HTML + Chart.js)

Create a file dashboard.html (serve with any static host):

<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Γ Utilization</title>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; }
    .wrap { max-width: 1100px; margin: 0 auto; }
    canvas { width: 100%; height: 320px; }
  </style>
</head>
<body>
<div class="wrap">
  <h1>Γ Utilization & Σψ</h1>
  <canvas id="gamma"></canvas>
  <h2 style="margin-top:32px">Pick a ψ kind</h2>
  <select id="kind">
    <option>hash</option>
    <option>ai</option>
    <option>quantum</option>
    <option>storage</option>
    <option>vdf</option>
  </select>
  <canvas id="psi"></canvas>
</div>

<script>
const API = "http://127.0.0.1:8080";

async function loadGamma() {
  const res = await fetch(API + "/api/series/gamma");
  return res.json();
}
async function loadPsi(kind) {
  const res = await fetch(API + "/api/series/psi_by_kind?kind=" + encodeURIComponent(kind));
  return res.json();
}

function tsToLabel(ts) {
  const d = new Date(ts * 1000);
  return d.toISOString().slice(11,19); // HH:MM:SS
}

(async () => {
  const data = await loadGamma();
  const labels = data.map(d => d.height);
  const util   = data.map(d => d.gamma_util);
  const spsi   = data.map(d => d.psi_total);
  const theta  = data.map(d => d.theta);

  const gctx = document.getElementById('gamma').getContext('2d');
  new Chart(gctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'Γ Utilization', data: util, yAxisID:'y', tension: 0.1 },
        { label: 'Σψ (total psi)', data: spsi, yAxisID:'y1', tension: 0.1 },
        { label: 'Θ (theta)', data: theta, yAxisID:'y1', borderDash:[5,5], tension: 0.1 }
      ]
    },
    options: {
      responsive: true,
      interaction: { mode:'index', intersect:false },
      scales: {
        y:  { type:'linear', position:'left', suggestedMin:0, suggestedMax:1 },
        y1: { type:'linear', position:'right', grid:{ drawOnChartArea:false } }
      }
    }
  });

  const ksel = document.getElementById('kind');
  const pctx = document.getElementById('psi').getContext('2d');

  async function drawKind(kind) {
    const rows = await loadPsi(kind);
    const labels = rows.map(d => d.height);
    const psi    = rows.map(d => d.psi);
    if (window._psiChart) window._psiChart.destroy();
    window._psiChart = new Chart(pctx, {
      type: 'line',
      data: { labels, datasets: [{ label: `ψ(${kind})`, data: psi, tension: 0.1 }] },
      options: { responsive:true }
    });
  }

  ksel.addEventListener('change', e => drawKind(e.target.value));
  drawKind(ksel.value);
})();
</script>
</body>
</html>

Open in a browser and watch charts update as the ingestor runs.

⸻

6) Operations
	•	Reorgs: This ingestor detects parent mismatch and rolls back one block. For deeper reorgs, loop the rollback until linkage matches.
	•	Backfill: Start from genesis by setting last_height to -1 (already default).
	•	Performance: For mainnet scale, batch inserts, add indexes, and consider a time-series DB. You can also shard by height ranges.
	•	Policy Changes: If Γ caps update on upgrades, refresh chain.getParams periodically and cache the new caps (store a policy_epoch column if needed).

⸻

7) Extensions
	•	Stacked Area Chart for per-kind ψ alongside Γ per-type caps.
	•	TPS / Gas overlays using execution metrics.
	•	Export CSV for external BI tools.
	•	Prometheus: expose /metrics and build Grafana dashboards.

⸻

8) Quick Commands

# 1) Create DB schema
sqlite3 indexer.sqlite < schema.sql

# 2) Run the ingestor
python ingest.py

# 3) Serve JSON API
uvicorn api:app --port 8080

# 4) Open dashboard (double-click dashboard.html or serve via `python -m http.server 8000`)


⸻

9) Data Dictionary
	•	blocks.theta — header Θ for the block.
	•	blocks.psi_total — Σψ across proof kinds (as exposed or computed).
	•	blocks.gamma_util — Γ utilization in [0, 1].
	•	psi_by_kind.kind — {hash, ai, quantum, storage, vdf} or your network’s set.
	•	psi_by_kind.psi — per-kind ψ.

⸻

Notes
	•	This guide assumes your node’s RPC is configured to expose PoIES summaries per block. If you only have raw proofs/receipts, aggregate ψ from those artifacts.
	•	Γ visualization here is policy-relative; it helps track how much budget is being utilized and whether caps are the binding constraint.

