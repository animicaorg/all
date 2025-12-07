# Useful Work Demo (Devnet with Quantum Jobs)

This walkthrough demonstrates the full integration of quantum computing as an external service in Animica's PoIES consensus. It shows how quantum job completions affect block acceptance.

## What This Demo Shows

1. **PoIES block validation** - How blocks are scored and accepted based on S ≥ Θ
2. **Quantum job lifecycle** - Submit job → Worker processes → Complete on-chain
3. **Score impact** - How quantum completion flips block from invalid to valid

## Prerequisites

- Python 3.11+
- Run commands from the repository root
- Dependencies: `pip install pytest pytest-asyncio cbor2 pyyaml`
- Set `RUN_INTEGRATION_TESTS=1` to enable integration tests

## Quick Demo (Integration Tests)

Run the comprehensive integration test suite:

```bash
# All three test suites
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_poies_quantum_integration.py -v
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_pq_transaction_validation.py -v
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_quantum_e2e_flow.py -v
```

### What Each Test Shows

#### 1. PoIES Quantum Integration (`test_poies_quantum_integration.py`)

Demonstrates how quantum jobs affect block acceptance:

```bash
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_poies_quantum_integration.py::test_quantum_job_flips_validity -v -s
```

**Output shows:**
```
Without quantum: S=2.8 < Θ=3.0 → REJECTED ❌
With quantum:    S=3.3 ≥ Θ=3.0 → ACCEPTED ✅
Quantum contribution: 0.5 nats
```

#### 2. PQ Transaction Validation (`test_pq_transaction_validation.py`)

Shows PQ cryptography on the transaction critical path:

```bash
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_pq_transaction_validation.py::test_pq_account_transaction_flow -v
```

**Demonstrates:**
- Generate Dilithium3 keypair
- Derive address from public key
- Sign transaction with PQ signature
- Verify signature successfully
- Reject on signature corruption

#### 3. Quantum E2E Flow (`test_quantum_e2e_flow.py`)

Shows the full quantum worker lifecycle:

```bash
RUN_INTEGRATION_TESTS=1 pytest tests/integration/test_quantum_e2e_flow.py::test_quantum_integration_with_poies_scoring -v -s
```

**Output shows:**
```
Quantum contribution: 0.528 nats
Without quantum: 2.500 nats (rejected)
With quantum: 3.028 nats (accepted)
Threshold: 3.000 nats
```

## Step-by-Step Quantum Demo

### Step 1: Start Quantum Worker

```bash
# In one terminal - start the quantum worker daemon
python -m mining.quantum_worker
```

The worker will:
- Poll for quantum jobs from contracts
- Execute circuits (using DevSim backend for dev)
- Submit results back on-chain

### Step 2: Submit a Quantum Job

```python
from mining.quantum_worker import QuantumWorker
import asyncio

async def submit_job():
    worker = QuantumWorker.create_from_env()
    await worker.start()
    
    # Submit a quantum circuit
    ticket = await worker.enqueue(
        width=5,           # 5 qubits
        depth=10,          # Circuit depth
        shots=256,         # Number of measurements
        trap_fraction=0.1, # 10% trap circuits for verification
        circuit_json=b'{"name": "bell_state"}',
        trap_seed=b"\x01" * 32
    )
    
    print(f"Job submitted: {ticket.task_id}")
    print(f"Status: {ticket.status}")
    
    # Wait for completion
    await asyncio.sleep(1.0)
    results = worker.pop_ready()
    
    for result in results:
        print(f"\nJob completed: {result.task_id}")
        print(f"Quantum units: {result.metrics['quantum_units']}")
        print(f"Trap ratio: {result.metrics['traps_ratio']:.2f}")
        print(f"QoS: {result.metrics['qos']:.2f}")
    
    await worker.stop()

asyncio.run(submit_job())
```

### Step 3: Observe PoIES Impact

```python
from consensus.scorer import aggregate_and_accept, default_score_hooks
from consensus.types import ProofType

# Mock policy
class Policy:
    def __init__(self):
        from collections import namedtuple
        Cap = namedtuple("Cap", "per_type_micro per_proof_micro_max")
        self.caps = {
            ProofType.QUANTUM: Cap(7_000_000, 5_000_000),
        }
        self.gamma_cap = 12_000_000
        self.weights = {
            ProofType.QUANTUM: {"k_units": 1.5, "t_min": 0.65, "t_target": 0.9}
        }

policy = Policy()
hooks = default_score_hooks(policy)

# Block without quantum job
base_entropy = 2_800_000  # 2.8 nats
theta = 3_000_000         # 3.0 nats

outcome_no_quantum = aggregate_and_accept(
    [], policy, theta_micro=theta, base_entropy_micro=base_entropy, hooks=hooks
)
print(f"Without quantum: S={outcome_no_quantum.score_micro/1e6:.1f} nats")
print(f"Accepted: {outcome_no_quantum.accepted}")

# Block with quantum job completion
proofs = [{
    "proof_id": b"Q" * 32,
    "proof_type": ProofType.QUANTUM,
    "metrics": {
        "quantum_units": 1.2,
        "traps_ratio": 0.88,
        "qos": 0.96,
    }
}]

outcome_with_quantum = aggregate_and_accept(
    proofs, policy, theta_micro=theta, base_entropy_micro=base_entropy, hooks=hooks
)
print(f"\nWith quantum: S={outcome_with_quantum.score_micro/1e6:.1f} nats")
print(f"Accepted: {outcome_with_quantum.accepted}")
print(f"Quantum contribution: {(outcome_with_quantum.score_micro - outcome_no_quantum.score_micro)/1e6:.3f} nats")
```

## Manual Devnet Setup (Full Stack)

For a complete devnet with contracts:

```bash
# 1) Start devnet node
python -m aicf.node --network devnet --rpc-addr 127.0.0.1 --rpc-port 18545 &
RPC_PID=$!
sleep 2

# 2) Deploy QuantumJobs and QuantumWorkers contracts
# (Assuming contracts are compiled)
python -m vm_py.cli.deploy \
  --manifest contracts/examples/quantum/quantum_jobs_manifest.json \
  --rpc http://127.0.0.1:18545

python -m vm_py.cli.deploy \
  --manifest contracts/examples/quantum/quantum_workers_manifest.json \
  --rpc http://127.0.0.1:18545

# 3) Start quantum worker
export AICF_URL=http://127.0.0.1:18545
python -m mining.quantum_worker &
WORKER_PID=$!

# 4) Submit a job via contract
python -m vm_py.cli.call \
  --contract QuantumJobs \
  --method submit_job \
  --args '{"job_id": "0x01", "job_spec": "0x...", "fee_escrow": 1000}' \
  --rpc http://127.0.0.1:18545

# 5) Mine a block (worker will complete job)
curl -X POST http://127.0.0.1:18545/ -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"animica_generate","params":[1]}'

# 6) Check block was accepted with quantum contribution
curl -X POST http://127.0.0.1:18545/ -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"eth_getBlockByNumber","params":["latest",true]}'

# Cleanup
kill $RPC_PID $WORKER_PID
```

## Understanding the Output

### Block Score Breakdown

```json
{
  "theta_micro": 3000000,           // Threshold (3.0 nats)
  "base_entropy_micro": 2800000,     // H(u) from hash mining (2.8 nats)
  "sum_after_gamma": 500000,         // Σψ from external proofs (0.5 nats)
  "per_type_after_gamma": {
    "QUANTUM": 500000,               // Quantum contribution
    "AI": 0,
    "STORAGE": 0
  },
  "distance_micro": 300000           // S - Θ = 0.3 nats (positive = accepted)
}
```

### Quantum Job Metrics

```json
{
  "quantum_units": 2.5,              // Normalized compute units
  "traps_ratio": 0.87,               // 87% trap success
  "qos": 0.95,                       // 95% quality of service
  "width": 5,                        // 5 qubits
  "depth": 10,                       // Circuit depth
  "shots": 256                       // Measurements
}
```

## Troubleshooting

### "Backend not available" errors

PQ crypto backends may not be installed. Tests will skip gracefully:
```bash
pytest tests/integration/test_pq_transaction_validation.py -v
# SKIPPED: Dilithium3 backend not available
```

This is expected in CI environments without liboqs. Tests pass when backend is present.

### Quantum worker not completing jobs

Ensure adequate time for DevSim backend:
- Default latency: 600ms
- Increase `QPU_SIM_LAT_MS` environment variable if needed
- Check worker is actually started and polling

### PoIES score not increasing

Verify:
- Quantum proof is well-formed with valid metrics
- Trap ratio is above `t_min` threshold (0.65)
- Policy caps are not preventing contribution

## Further Reading

- **PoIES Overview:** `docs/consensus/poies_overview.md`
- **PQ Identity:** `docs/pq/pq_identity.md`
- **Quantum Proofs:** `docs/quantum/OVERVIEW.md`
- **Contracts:** `contracts/examples/quantum/`
- **Worker Implementation:** `mining/quantum_worker.py`

## Contract Source

Quantum contracts are located in:
- `contracts/examples/quantum/quantum_jobs_contract.py` - Job management
- `contracts/examples/quantum/quantum_workers_contract.py` - Worker registry

See source for full API documentation.
