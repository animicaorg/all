# Hybrid Useful Work Demo

This guide demonstrates running Animica with multiple types of useful work contributing to consensus: hash work, AI, and quantum.

## Overview

In this demo, we'll:
1. Deploy HashJobs and HashWorkers contracts
2. Start multiple hash workers (CPU, GPU mock, ASIC mock)
3. Post hash jobs with varying difficulty
4. Submit AI and Quantum work alongside hash work
5. Observe PoIES scoring with hybrid proofs

## Prerequisites

- Animica devnet running
- Python 3.12+ with dependencies installed
- Node.js for SDK/frontend (optional)

## Setup

### 1. Start Devnet

```bash
# Start local devnet node
cd /path/to/animica
./setup.sh devnet

# In another terminal, check node status
curl http://localhost:8545/health
```

### 2. Deploy Contracts

```bash
# Deploy HashJobs contract
python -m vm_py.cli.deploy \
    --manifest contracts/examples/hash_work/hash_jobs.py \
    --chain-id 1337 \
    --rpc http://localhost:8545

# Deploy HashWorkers contract
python -m vm_py.cli.deploy \
    --manifest contracts/examples/hash_work/hash_workers.py \
    --chain-id 1337 \
    --rpc http://localhost:8545

# Note the deployed contract addresses
export HASH_JOBS_ADDRESS=<address>
export HASH_WORKERS_ADDRESS=<address>
```

### 3. Register Workers

```bash
# Register CPU worker
python -m python.animica.cli register-worker \
    --contract $HASH_WORKERS_ADDRESS \
    --device-type CPU \
    --worker-key ~/.animica/worker_cpu.key

# Register GPU worker
python -m python.animica.cli register-worker \
    --contract $HASH_WORKERS_ADDRESS \
    --device-type GPU \
    --worker-key ~/.animica/worker_gpu.key

# Register ASIC worker
python -m python.animica.cli register-worker \
    --contract $HASH_WORKERS_ADDRESS \
    --device-type ASIC \
    --worker-key ~/.animica/worker_asic.key
```

## Running Workers

### CPU Worker

```bash
# Terminal 1: CPU worker
export ANIMICA_RPC_URL=http://localhost:8545
export ANIMICA_CHAIN_ID=1337
export HASH_BACKEND_TYPE=cpu
export HASH_WORKER_ADDRESS=<cpu_worker_address>
export HASH_STATE_FILE=/tmp/hash_worker_cpu.json

python -m python.animica.hash_worker.cli start --backend cpu
```

### GPU Worker (Mock)

```bash
# Terminal 2: GPU worker (mock)
export ANIMICA_RPC_URL=http://localhost:8545
export ANIMICA_CHAIN_ID=1337
export HASH_BACKEND_TYPE=gpu
export HASH_WORKER_ADDRESS=<gpu_worker_address>
export HASH_STATE_FILE=/tmp/hash_worker_gpu.json

python -m python.animica.hash_worker.cli start --backend gpu
```

### ASIC Worker (Mock)

```bash
# Terminal 3: ASIC worker (mock)
export ANIMICA_RPC_URL=http://localhost:8545
export ANIMICA_CHAIN_ID=1337
export HASH_BACKEND_TYPE=asic
export HASH_WORKER_ADDRESS=<asic_worker_address>
export HASH_STATE_FILE=/tmp/hash_worker_asic.json

python -m python.animica.hash_worker.cli start --backend asic
```

### Quantum Worker (Mock)

```bash
# Terminal 4: Quantum worker (mock)
export ANIMICA_RPC_URL=http://localhost:8545
export ANIMICA_CHAIN_ID=1337
export HASH_BACKEND_TYPE=quantum
export HASH_WORKER_ADDRESS=<quantum_worker_address>
export HASH_STATE_FILE=/tmp/hash_worker_quantum.json

python -m python.animica.hash_worker.cli start --backend quantum
```

## Posting Jobs

### Post SHA-256 Job (Easy)

```bash
python -m python.animica.cli post-hash-job \
    --contract $HASH_JOBS_ADDRESS \
    --algorithm SHA256 \
    --input "test data 1" \
    --target-bits 12 \
    --max-iterations 1000000
```

### Post SHA-256D Job (Medium)

```bash
python -m python.animica.cli post-hash-job \
    --contract $HASH_JOBS_ADDRESS \
    --algorithm SHA256D \
    --input "test data 2" \
    --target-bits 16 \
    --max-iterations 10000000
```

### Post Scrypt Job (Hard)

```bash
python -m python.animica.cli post-hash-job \
    --contract $HASH_JOBS_ADDRESS \
    --algorithm SCRYPT \
    --input "test data 3" \
    --target-bits 16 \
    --scrypt-n 16384 \
    --scrypt-r 8 \
    --scrypt-p 1 \
    --max-iterations 1000000
```

## Hybrid Workload

### Scenario: Mixed Work in Single Block

This scenario posts multiple types of work to be included in a single block:

```bash
# Post hash job
JOB1=$(python -m python.animica.cli post-hash-job \
    --contract $HASH_JOBS_ADDRESS \
    --algorithm SHA256 \
    --input "block 100" \
    --target-bits 14 \
    --output-job-id)

# Post AI job
JOB2=$(python -m python.animica.aicf.cli queue_submit \
    --ai --model "animica/llm-small" \
    --prompt "Summarize blockchain consensus" \
    --output-task-id)

# Post quantum job
JOB3=$(python -m python.animica.quantum.cli submit \
    --circuit circuits/bell_state.qasm \
    --shots 256 \
    --output-task-id)

# Wait for results
echo "Waiting for job completions..."
sleep 30

# Check results
python -m python.animica.cli get-hash-result --job-id $JOB1
python -m python.animica.aicf.cli get_result --task-id $JOB2
python -m python.animica.quantum.cli get_result --task-id $JOB3
```

## Observing PoIES Scores

### Check Block Scores

```bash
# Get latest block
BLOCK=$(curl -s http://localhost:8545/api/v1/blocks/latest | jq -r '.height')

# Get block details with PoIES breakdown
curl http://localhost:8545/api/v1/blocks/$BLOCK/poies | jq
```

Expected output:
```json
{
  "block_height": 100,
  "accepted": true,
  "score_micro": 8500000,
  "theta_micro": 6000000,
  "base_entropy_micro": 500000,
  "breakdown": {
    "per_type_after_gamma": {
      "HASH": 1200000,
      "HASH_WORK": 2300000,
      "AI": 2800000,
      "QUANTUM": 1700000
    },
    "proof_count": {
      "HASH_WORK": 3,
      "AI": 1,
      "QUANTUM": 1
    }
  }
}
```

### Monitor Worker Performance

```bash
# Query worker stats
python -m python.animica.cli worker-stats \
    --contract $HASH_WORKERS_ADDRESS \
    --worker $CPU_WORKER_ADDRESS

# Output:
# {
#   "address": "anim1...",
#   "device_type": "CPU",
#   "jobs_completed": 45,
#   "total_iterations": 2345678,
#   "avg_hashrate": 1200000,
#   "success_rate": 0.95
# }
```

## Performance Comparison

### Benchmark All Backends

Create a benchmark script:

```python
#!/usr/bin/env python3
# benchmark_all.py

import subprocess
import json
from time import time

backends = ["cpu", "gpu", "asic", "quantum"]
algorithms = ["SHA256", "SHA256D", "SCRYPT"]
targets = [12, 16, 20]

results = []

for backend in backends:
    for algo in algorithms:
        for target in targets:
            print(f"Testing {backend} / {algo} / {target} bits...")
            
            start = time()
            result = subprocess.run([
                "python", "-m", "python.animica.hash_worker.cli",
                "test-job",
                "--backend", backend,
                "--algorithm", algo,
                "--target", str(target),
                "--max-iterations", "1000000"
            ], capture_output=True, text=True)
            elapsed = time() - start
            
            success = result.returncode == 0
            
            results.append({
                "backend": backend,
                "algorithm": algo,
                "target_bits": target,
                "success": success,
                "time": elapsed
            })

# Print results table
print("\n{:<10} {:<10} {:<12} {:<10} {:<10}".format(
    "Backend", "Algorithm", "Target", "Success", "Time (s)"
))
print("-" * 60)
for r in results:
    print("{:<10} {:<10} {:<12} {:<10} {:<10.3f}".format(
        r["backend"], r["algorithm"], r["target_bits"], 
        "✓" if r["success"] else "✗", r["time"]
    ))
```

Run benchmark:
```bash
python benchmark_all.py
```

Expected output:
```
Backend    Algorithm  Target       Success    Time (s)  
------------------------------------------------------------
cpu        SHA256     12           ✓          0.015     
cpu        SHA256     16           ✓          0.089     
cpu        SHA256     20           ✗          5.123     
cpu        SHA256D    12           ✓          0.031     
gpu        SHA256     12           ✓          0.014     
gpu        SHA256     16           ✓          0.087     
asic       SHA256     12           ✓          0.013     
quantum    SHA256     16           ✓          0.091     
...
```

## Production Considerations

### 1. Worker Distribution

For production, distribute workers across multiple machines:

```
Load Balancer
    |
    ├── CPU Worker Pool (10 workers)
    ├── GPU Worker Pool (5 workers)
    ├── ASIC Worker Pool (20 workers)
    └── Quantum Worker Pool (2 workers)
```

### 2. Job Queue Management

Implement priority queue for jobs:
- High priority: Time-sensitive hash work
- Medium priority: Standard hash jobs
- Low priority: Opportunistic work

### 3. Reward Distribution

Track worker contributions and distribute rewards:

```python
# Calculate reward share
worker_share = (worker_iterations / total_iterations) * block_reward

# Account for device type multiplier
if device_type == "QUANTUM":
    worker_share *= 1.5  # Bonus for quantum
elif device_type == "ASIC":
    worker_share *= 0.8  # Penalty for specialized hardware
```

### 4. Monitoring

Set up monitoring dashboard:
- Worker uptime and health
- Job completion rate
- Average hashrate by device type
- PoIES score contribution over time

## Troubleshooting

### Worker Not Picking Up Jobs

```bash
# Check worker logs
tail -f /tmp/hash_worker_cpu.log

# Verify worker registration
python -m python.animica.cli get-worker \
    --contract $HASH_WORKERS_ADDRESS \
    --address $WORKER_ADDRESS

# Check algorithm capabilities
python -m python.animica.cli list-worker-capabilities \
    --contract $HASH_WORKERS_ADDRESS \
    --address $WORKER_ADDRESS
```

### Low PoIES Scores

```bash
# Check difficulty settings
curl http://localhost:8545/api/v1/consensus/theta

# Adjust job difficulty
# Lower target_bits for more frequent solutions
```

### High Rejection Rate

```bash
# Check worker accuracy
python -m python.animica.cli worker-stats \
    --contract $HASH_WORKERS_ADDRESS \
    --worker $WORKER_ADDRESS \
    --verbose

# Review failed submissions
python -m python.animica.cli list-failed-jobs \
    --worker $WORKER_ADDRESS \
    --limit 10
```

## Sample Configurations

See the included sample environment files:
- `hash_worker.cpu.env.example` - CPU worker config
- `hash_worker.gpu.env.example` - GPU worker config
- `hash_worker.asic.env.example` - ASIC worker config
- `hash_worker.quantum.env.example` - Quantum worker config

## Next Steps

- Explore [Hash Work Overview](./hash_work_overview.md) for detailed architecture
- Read [PoIES Consensus](../consensus/poies.md) for scoring details
- Check [External Services](./external_services.md) for other work types
