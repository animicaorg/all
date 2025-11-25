# Oracle (attested & reporter-fed)

A minimal but *practical* price oracle that supports two complementary flows:

1) **Reporter-fed (push)** — authorized feeders submit fresh values on-chain.
2) **Attested fetch (pull via AICF)** — the contract **enqueues** a deterministic off-chain job
   to fetch/aggregate prices in a TEE; on the **next block** it **consumes** the result using the
   Capabilities runtime (`ai_enqueue` → `read_result`) and updates the feed.

Both paths produce identical storage shapes and events. This example is intentionally small but
illustrates how to build *deterministic* off-chain data ingress on Animica without breaking
consensus rules.

---

## Why this example?

- Shows **capability-driven oracles** (request now, consume next block) with proofs flowing through
  the consensus pipeline (see `capabilities/` and `proofs/` modules).
- Demonstrates a safe **reporter push** path (role-gated) for simple deployments and testnets.
- Models **rounds**, **timestamps**, **decimals**, and **TWAP** helpers you can extend.

---

## On-chain interface (overview)

> See `contract.py` for the authoritative ABI; `manifest.json` contains the machine-readable ABI.

### Read methods

- `get_latest(pair: bytes32) -> (value: int, decimals: int, ts: u64, round_id: u64, source: bytes32)`
- `has_pair(pair: bytes32) -> bool`
- `get_twap(pair: bytes32, lookback_secs: u32) -> (value: int, ts: u64)`  
  Simple time-weighted average over recent rounds (ring buffer).

### Write methods

- `set_feeder(addr: address, allowed: bool)` — owner/roles only.
- `set_pair_decimals(pair: bytes32, decimals: u8)` — owner/roles only.
- **Reporter push:**  
  `submit(pair: bytes32, value: int, ts: u64, source: bytes32) -> round_id: u64`
- **Attested fetch (AICF):**
  - `request_fetch(pair: bytes32, spec: bytes)` → `task_id: bytes32`  
    Enqueues a job (AICF) with a compact JSON spec (endpoints, aggregation rule).
  - `consume_fetch(pair: bytes32, task_id: bytes32) -> (ok: bool, round_id: u64)`  
    Reads the next-block result via `read_result(task_id)` and updates storage.

### Events

- `FeederSet(addr, allowed)`  
- `PairConfigured(pair, decimals)`
- `FetchRequested(pair, task_id)`
- `FetchConsumed(pair, task_id, ok)`
- `PriceUpdated(pair, value, decimals, round_id, ts, source)`

**Keying**  
Pairs use a canonical `bytes32` key (e.g., `sha3_256(b"BTC/USD")`). See helper in tests/scripts.

---

## Determinism & security model

- **Attested fetch flow** is split across **two blocks**:
  1) `request_fetch` enqueues a job (deterministic `task_id` = H(chainId|height|txHash|caller|payload)).
  2) After the proof is included and resolved by the node, `consume_fetch` reads the result from the
     per-block capabilities store. This avoids nondeterministic I/O inside a single block.
- **Proofs** (TEE attestations, traps/QoS for AI/Quantum) are validated by `proofs/` and influence
  acceptance under PoIES; the contract *does not* re-verify proofs — it only consumes the normalized
  result record by `task_id`.
- **Reporter push** path is role-gated; values should include `ts` (seconds) and a `source` tag
  (e.g., `sha3_256(b"coingecko:v1")`) for auditability.
- **Staleness**: the contract enforces a maximum tolerated skew (configurable constant in code)
  for reporter submissions and for consumed results.

---

## Data flow (attested)

request_fetch()                AICF / Providers                       consensus & capabilities

⸻

Tx enqueues job     —>  queue/assign (attested worker)  —>  provider runs in TEE, produces
(task_id derived)         fetch+aggregate per spec,                   proof & result digest
returns result bytes                        (included next block)

consume_fetch()     <—  result resolved by node  <—  proofs/ → capabilities/jobs/resolver
(read_result)                   (next block)                   populates result_store(task_id)

`consume_fetch` will fail (return `ok=false`) if called too early (result not yet available)
or if the result does not pass normalization limits (size/format).

---

## Layout

contracts/examples/oracle/
├─ contract.py          # the oracle implementation (deterministic Python-VM)
├─ manifest.json        # ABI + metadata (used by SDK and studio)
├─ tests_local.py       # pure local VM smoke tests
└─ deploy_and_test.py   # end-to-end: build → deploy → request → consume → read

> This README focuses on usage. See the source files for exact interfaces and constants.

---

## Quickstart

### 0) Pre-reqs

- Python 3.10+  
- Monorepo checkout with `vm_py/`, `sdk/python/`, and `capabilities/` available in the path (all
  handled by the included scripts).  
- Optional: a running **devnet** (`tests/devnet/docker-compose.yml`) or your local node.

Set a `.env` (see `contracts/.env.example`):

RPC_URL=http://127.0.0.1:8545
CHAIN_ID=1337
DEPLOYER_MNEMONIC=“enlist hip relief stomach … (dev only)”

### 1) Build

make -C contracts build

or directly:

python -m contracts.tools.build_package 
–source contracts/examples/oracle/contract.py 
–manifest contracts/examples/oracle/manifest.json 
–out contracts/build

### 2) Local VM smoke (no network)

python contracts/examples/oracle/deploy_and_test.py –no-network

or:

python contracts/examples/oracle/tests_local.py

### 3) Deploy to devnet

python -m contracts.examples.oracle.deploy_and_test 
–rpc $RPC_URL –chain-id $CHAIN_ID –mnemonic “$DEPLOYER_MNEMONIC”

The script prints the deployed address and runs a basic reporter push + attested roundtrip.

---

## Using the oracle

### A) Reporter-fed push

1) **Authorize** a feeder (owner):

python -m contracts.tools.call 
–address <ORACLE_ADDR> 
–manifest contracts/examples/oracle/manifest.json 
–func set_feeder –args ‘[”<FEEDER_ADDR>”, true]’

2) **Submit** a price (from feeder account):

python -m contracts.tools.call 
–address <ORACLE_ADDR> 
–manifest contracts/examples/oracle/manifest.json 
–func submit 
–args ‘[“0x”, 4321000000, 1717000000, “0x”]’

Where:
- `PAIR32 = sha3_256("BTC/USD")`
- `value` is integer with your `decimals` precision (configure via `set_pair_decimals`)
- `ts` is unix seconds
- `source` is a 32-byte tag (e.g., `sha3_256("coingecko:v1")`)

3) **Read latest**:

python -m contracts.tools.call 
–address <ORACLE_ADDR> 
–manifest contracts/examples/oracle/manifest.json 
–func get_latest –args ‘[“0x”]’

### B) Attested fetch (AICF)

1) **Request** a fetch:

python -m contracts.tools.call 
–address <ORACLE_ADDR> 
–manifest contracts/examples/oracle/manifest.json 
–func request_fetch 
–args ‘[“0x”, “0x<CBOR_OR_JSON_SPEC_BYTES>”]’

- The **spec** is a compact JSON/CBOR blob describing sources and an aggregation rule,
  e.g. `{"endpoints":[{"u":"https://api.exchange1/...","k":"price"}],"agg":"median","decimals":8}`.
  The contract only stores the spec hash and sends the payload to AICF; the node validates proofs.

2) **Wait one block** (the provider completes, proof is included).

3) **Consume**:

python -m contracts.tools.call 
–address <ORACLE_ADDR> 
–manifest contracts/examples/oracle/manifest.json 
–func consume_fetch 
–args ‘[“0x”, “0x<TASK_ID32>”]’

4) **Read** latest / **TWAP**:

latest

python -m contracts.tools.call 
–address <ORACLE_ADDR> 
–manifest contracts/examples/oracle/manifest.json 
–func get_latest –args ‘[“0x”]’

10-minute TWAP

python -m contracts.tools.call 
–address <ORACLE_ADDR> 
–manifest contracts/examples/oracle/manifest.json 
–func get_twap –args ‘[“0x”, 600]’

---

## Design notes

- **Storage model**
  - `round_id` monotonically increases per pair.
  - Ring buffer of the last *N* rounds (configurable constant) enabling O(1) TWAP.
  - `decimals` stored per pair; `submit` enforces matching precision.
- **Limits**
  - Result bytes are size-capped and schema-checked before update.
  - `ts` skew must be within `MAX_SKEW_SECS`.
- **Roles**
  - `owner` can set feeders and pair metadata.
  - Optional **role-based access control** is implemented via `stdlib/access/roles.py`.
- **Events**
  - Emitted on every state transition; indexers / explorer can track updates.

---

## Studio integration

- **studio-wasm**: Compile & simulate the oracle methods entirely in the browser.
- **studio-web**: Open the oracle template, configure pairs, request fetch, and watch results.
- **studio-services**: Verify the source after deploy (`/verify`) or pin artifacts.

---

## Troubleshooting

- `consume_fetch` returns `ok=false`: result not available yet (call it next block) or the job failed SLA.
- Receipt shows **revert**: check `decimals` mismatch or staleness; ensure caller has feeder role for `submit`.
- Pair not found: call `set_pair_decimals` before first update, or let `request_fetch` initialize metadata.

---

## Extending

- Add **median-of-reporters** on the push path (n-of-m threshold).
- Include **confidence** / **deviation guards** vs previous round.
- Add **circuit-breakers** (pause updates on abnormal variance).
- Implement **multi-asset** TWAPs or **VWAP** based on size metadata returned by the attested job.

---

## Scripts

- `tests_local.py` — pure VM smoke for submit / request / consume / read (no node).
- `deploy_and_test.py` — one-shot deploy and both flows on a devnet using the Python SDK.

---

## Caveats

- This example does **not** perform HTTP in-contract; external fetch happens in attested workers.
- Next-block consumption is **by design**; do not attempt to read the result in the same block.

