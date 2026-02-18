# ENA Architecture

## System Overview

```
                                    ┌─────────────────────────────────┐
                                    │      End User / Miner           │
                                    └───────────┬─────────────────────┘
                                                │
                                                │ animica ena commands
                                                ▼
                     ┌──────────────────────────────────────────────────┐
                     │          Animica CLI (ena.py)                     │
                     │  - models, pricing, infer, deposit, status        │
                     │  - Wallet integration                             │
                     │  - Auto payment handling                          │
                     └───────────┬──────────────────────────────────────┘
                                 │
                                 │ HTTP/JSON
                                 ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                           ENA FastAPI Server                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Payment    │  │    Model     │  │     Rate     │  │   Circuit    │  │
│  │     Gate     │  │   Registry   │  │   Limiter    │  │   Breaker    │  │
│  │              │  │              │  │              │  │              │  │
│  │ - Per-call   │  │ - Versioning │  │ - Per-addr   │  │ - RPC fail   │  │
│  │ - Credit     │  │ - Aliases    │  │ - Per-IP     │  │ - Auto retry │  │
│  │ - Replay     │  │ - Hot-reload │  │ - Token bkt  │  │ - Recovery   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                  │                  │                  │          │
│         └──────────────────┴──────────────────┴──────────────────┘          │
│                                     │                                       │
│  ┌─────────────────────────────────┴────────────────────────────────────┐  │
│  │                          Request Handler                              │  │
│  │  1. Rate limit check                                                  │  │
│  │  2. Input validation                                                  │  │
│  │  3. Payment verification (via RPC)                                    │  │
│  │  4. Model loading                                                     │  │
│  │  5. Inference execution                                               │  │
│  │  6. Usage metering                                                    │  │
│  │  7. Database logging                                                  │  │
│  └───────────────────────────────┬───────────────────────────────────────┘  │
│                                  │                                          │
│         ┌────────────────────────┴────────────────────────┐                │
│         │                    │                    │        │                │
│         ▼                    ▼                    ▼        ▼                │
│  ┌────────────┐      ┌────────────┐      ┌────────────┐  │                │
│  │ Inference  │      │  Database  │      │    RPC     │  │                │
│  │  Engine    │      │  (SQLite)  │      │   Client   │  │                │
│  │            │      │            │      │            │  │                │
│  │ - CPU only │      │ - Used txs │      │ - Timeout  │  │                │
│  │ - Metering │      │ - Credits  │      │ - Retry    │  │                │
│  │ - Tokenize │      │ - Logs     │      │ - Backoff  │  │                │
│  └────────────┘      └────────────┘      └─────┬──────┘  │                │
│                                                 │         │                │
└─────────────────────────────────────────────────┼─────────┘                │
                                                  │                          │
                                                  │ JSON-RPC                 │
                                                  ▼                          │
                           ┌──────────────────────────────────────┐          │
                           │    Animica Blockchain RPC            │          │
                           │  - Transaction verification          │          │
                           │  - Balance queries                   │          │
                           │  - Nonce management                  │          │
                           └──────────────────────────────────────┘          │
                                                                              │
```

## Component Details

### 1. Animica CLI (`python/animica/cli/ena.py`)

**Responsibilities**:
- User interface for ENA service
- Wallet integration
- Automatic payment transaction creation
- Rich terminal output

**Commands**:
```
animica ena models      # List available models
animica ena pricing     # Get pricing info
animica ena infer       # Run inference with payment
animica ena deposit     # Deposit credits
animica ena status      # Check transaction status
```

### 2. ENA FastAPI Server (`ena/services/ena_node/main.py`)

**Endpoints**:
```
GET  /v1/health               # Health check
GET  /v1/models               # List models
GET  /v1/pricing              # Get pricing
POST /v1/infer                # Run inference (payment required)
POST /admin/set_default_model # Set default model (admin)
POST /admin/set_alias         # Set model alias (admin)
POST /admin/reload_models     # Reload models (admin)
```

**Request Flow**:
```
1. Request received
2. Extract client IP
3. Check rate limits (per-address + per-IP)
4. Validate input (prompt, model, payment)
5. Verify payment:
   - Per-call: Verify tx on blockchain
   - Credit: Deduct from balance
6. Load model (cache if already loaded)
7. Run inference
8. Calculate cost
9. Log request to database
10. Return result + receipt
```

### 3. Payment Gate

**Per-Call Transaction Mode**:
```
User Request                  ENA Service                  Blockchain
     │                              │                            │
     │ 1. Create payment tx         │                            │
     ├─────────────────────────────►│                            │
     │                              │ 2. Submit tx               │
     │                              ├───────────────────────────►│
     │                              │                            │
     │ 3. Include tx_hash in req    │                            │
     ├─────────────────────────────►│                            │
     │                              │ 4. Verify tx               │
     │                              ├───────────────────────────►│
     │                              │◄───────────────────────────┤
     │                              │ 5. Mark as used            │
     │                              │                            │
     │ 6. Return result + receipt   │                            │
     │◄─────────────────────────────┤                            │
```

**Credit Mode**:
```
User                          ENA Service                  Database
  │                                 │                           │
  │ 1. Deposit tx to service addr   │                           │
  ├────────────────────────────────►│                           │
  │                                 │ 2. Track deposit          │
  │                                 ├──────────────────────────►│
  │                                 │                           │
  │ 3. Inference request            │                           │
  ├────────────────────────────────►│                           │
  │                                 │ 4. Check balance          │
  │                                 ├──────────────────────────►│
  │                                 │◄──────────────────────────┤
  │                                 │ 5. Deduct credits         │
  │                                 ├──────────────────────────►│
  │                                 │ 6. Run inference          │
  │                                 │                           │
  │ 7. Return result + receipt      │                           │
  │◄────────────────────────────────┤                           │
```

### 4. Animica RPC Client (`ena/animica/animica_rpc.py`)

**Features**:
- Automatic retries with exponential backoff
- Circuit breaker for RPC failures
- Connection pooling
- Timeout handling
- Error decoding

**Circuit Breaker States**:
```
         ┌──────────┐
         │  CLOSED  │ ◄─── Normal operation
         └────┬─────┘
              │ Failures exceed threshold
              ▼
         ┌──────────┐
         │   OPEN   │ ◄─── Fail fast
         └────┬─────┘
              │ Timeout expires
              ▼
       ┌────────────┐
       │ HALF-OPEN  │ ◄─── Test recovery
       └────┬───┬───┘
            │   │
    Success │   │ Failure
            │   │
            ▼   ▼
         CLOSED  OPEN
```

### 5. Database Schema (`ena/services/ena_node/database.py`)

**Tables**:

```sql
-- Replay protection
CREATE TABLE used_transactions (
    tx_hash TEXT PRIMARY KEY,
    payer TEXT NOT NULL,
    amount INTEGER NOT NULL,
    used_at REAL NOT NULL,
    request_id TEXT NOT NULL
);

-- Credit balances
CREATE TABLE credit_balances (
    address TEXT PRIMARY KEY,
    balance INTEGER NOT NULL,
    updated_at REAL NOT NULL
);

-- Audit log
CREATE TABLE request_logs (
    request_id TEXT PRIMARY KEY,
    payer TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL,
    tx_hash TEXT,
    amount_paid INTEGER NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    success INTEGER NOT NULL,
    error TEXT
);
```

### 6. Rate Limiter (`ena/services/ena_node/rate_limiter.py`)

**Token Bucket Algorithm**:
```
Capacity: 100 tokens
Refill rate: 100/3600 = 0.0278 tokens/sec

Time: 0s    Tokens: 100 ██████████
          Request 1 ✓  Tokens: 99  █████████▉

Time: 10s   Refill +0.278
            Tokens: 99.278

Time: 3600s Refill complete
            Tokens: 100 ██████████
```

**Enforcement**:
- Check both per-address AND per-IP
- Both must pass for request to proceed
- Gradual refill prevents burst abuse

### 7. Model Registry (`ena/model_registry.py`)

**Structure**:
```
models/
├── ena.tiny.v1.json       ← Metadata
│   {
│     "name": "ena.tiny.v1",
│     "version": "0.1.0",
│     "path": "./models/tiny.bin",
│     "max_tokens": 500
│   }
├── tiny.bin               ← Model weights
└── ...

Aliases:
  ena.latest → ena.tiny.v1
  ena.fast   → ena.tiny.v1

Default: ena.tiny.v1
```

## Data Flow

### Successful Request

```
1. User: animica ena infer "Hello!"
   └─► Create payment tx to ENA service address
   └─► Submit tx to blockchain
   └─► Get tx_hash

2. CLI: POST /v1/infer
   {
     "prompt": "Hello!",
     "payment": {
       "mode": "per_call_tx",
       "payer": "anim1abc...",
       "tx_hash": "0x123..."
     }
   }

3. Server: Validate & verify
   ├─► Check rate limits ✓
   ├─► Validate input ✓
   ├─► Verify tx on blockchain ✓
   ├─► Check tx not used before ✓
   └─► Mark tx as used

4. Server: Execute
   ├─► Load model (cached)
   ├─► Run inference
   ├─► Count tokens
   └─► Calculate cost

5. Server: Log & respond
   ├─► Log to database
   └─► Return result + receipt

6. CLI: Display result
   └─► Show answer, usage, receipt
```

### Failed Request (Rate Limited)

```
1. User: Make 101st request in hour

2. Server: Check rate limits
   ├─► Check address: 101 > 100 ✗
   └─► Return 429 Too Many Requests

3. CLI: Display error
   └─► "Error: Rate limit exceeded"
```

### Failed Request (Payment)

```
1. User: Submit with wrong tx

2. Server: Verify payment
   ├─► Fetch tx from blockchain
   ├─► Check recipient: Wrong address ✗
   └─► Return 400 Bad Request

3. CLI: Display error
   └─► "Payment verification failed: Invalid recipient"
```

## Security Layers

```
┌─────────────────────────────────────────┐
│         Layer 1: Rate Limiting          │  ← Prevent abuse
│  - Per-address: 100 req/hr              │
│  - Per-IP: 200 req/hr                   │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│      Layer 2: Input Validation          │  ← Prevent injection
│  - Address format (bech32)              │
│  - Tx hash format (0x + 64 hex)         │
│  - Prompt length (max 2000)             │
│  - Token limits (max 500)               │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│    Layer 3: Payment Verification        │  ← Prevent theft
│  - Blockchain verification              │
│  - Amount validation                    │
│  - Address matching                     │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│      Layer 4: Replay Protection         │  ← Prevent reuse
│  - Track used tx hashes                 │
│  - Permanent record                     │
│  - Database integrity                   │
└─────────────────┬───────────────────────┘
                  │
┌─────────────────▼───────────────────────┐
│        Layer 5: Audit Logging           │  ← Track activity
│  - All requests logged                  │
│  - Payer identification                 │
│  - Usage statistics                     │
└─────────────────────────────────────────┘
```

## Deployment Options

### Docker

```
┌─────────────────────────────────────┐
│        Docker Container             │
│  ┌───────────────────────────────┐  │
│  │      ENA Service              │  │
│  │  (FastAPI + Uvicorn)          │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │      Volume: /data            │  │
│  │  - ena.db                     │  │
│  │  - ena.log                    │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │      Volume: /app/models      │  │
│  │  - Model files                │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
         │
         │ Port 8080
         ▼
    Host Network
```

### Systemd

```
┌─────────────────────────────────────┐
│          System Services            │
│  ┌───────────────────────────────┐  │
│  │    ena-node.service           │  │
│  │  User: ena                    │  │
│  │  WorkingDir: /opt/ena         │  │
│  │  Restart: on-failure          │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │    Journal Logging            │  │
│  │  SyslogIdentifier: ena-node   │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

## Monitoring & Observability

### Logs

```
2024-01-15 10:30:45 - INFO - ENA node started
2024-01-15 10:30:50 - INFO - Inference request: 550e8400-...
2024-01-15 10:30:50 - DEBUG - RPC call: tx.getTransaction
2024-01-15 10:30:51 - INFO - Payment verified: 0xabc123...
2024-01-15 10:30:52 - INFO - Inference complete: 18 tokens
2024-01-15 10:30:52 - WARNING - Rate limit exceeded: anim1xyz...
```

### Database Queries

```sql
-- Recent requests by user
SELECT * FROM request_logs 
WHERE payer = 'anim1...' 
ORDER BY timestamp DESC 
LIMIT 10;

-- Usage statistics
SELECT 
  COUNT(*) as requests,
  SUM(total_tokens) as tokens,
  SUM(amount_paid) as total_paid
FROM request_logs
WHERE timestamp > strftime('%s', 'now', '-1 day');

-- Failed requests
SELECT * FROM request_logs
WHERE success = 0
ORDER BY timestamp DESC;
```

## Performance Characteristics

### Latency Breakdown

```
Total Request Time: ~150-500ms

1. Rate limit check:      <1ms
2. Input validation:      <1ms  
3. RPC verification:      50-200ms (network)
4. Database operations:   1-5ms
5. Model inference:       50-200ms (CPU)
6. Response formatting:   <1ms
```

### Throughput

```
Per-Address Limit: 100 req/hr = 0.028 req/sec
Per-IP Limit:      200 req/hr = 0.056 req/sec

Theoretical Max:   Limited by rate limiter
Practical Max:     ~50-100 concurrent users
```

### Resource Usage

```
CPU:     Low (no GPU)
Memory:  ~200-500MB (depends on model)
Disk:    ~100MB (database + logs)
Network: ~1-10 KB/request
```

## Future Architecture

### Horizontal Scaling

```
                Load Balancer
                      │
        ┌─────────────┼─────────────┐
        │             │             │
        ▼             ▼             ▼
    ENA Node 1    ENA Node 2    ENA Node 3
        │             │             │
        └─────────────┼─────────────┘
                      │
                 PostgreSQL
              (Shared Database)
```

### Distributed Inference

```
    ENA API Node
         │
    ┌────┴────┐
    │ Queue   │
    └────┬────┘
         │
    ┌────┴────┬─────────┬─────────┐
    ▼         ▼         ▼         ▼
Worker 1  Worker 2  Worker 3  Worker N
(CPU)     (CPU)     (GPU)     (GPU)
```

---

**Last Updated**: 2024-02-18  
**Version**: 0.1.0
