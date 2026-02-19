# Quantum CLI Quick Reference

## Overview
Comprehensive quantum CLI commands for Animica blockchain quantum computation features.

## Command Tree
```
animica quantum
├── status                      # Show quantum service status
├── credits <address>           # Show quantum contribution credits
├── jobs
│   ├── list                    # List quantum jobs
│   └── submit                  # Submit quantum job
└── contribute
    ├── register                # Register as worker
    ├── run                     # Run workload
    ├── status                  # Show worker status
    ├── watch <job-id>          # Monitor job progress
    ├── start                   # Start worker
    └── stop                    # Stop worker
```

## Quick Commands

### Check Service Status
```bash
animica quantum status
animica quantum status --json
```

### View Credits
```bash
animica quantum credits 0x1234...
animica quantum credits anm1abc... --json
```

### List Jobs
```bash
animica quantum jobs list
animica quantum jobs list --status RUNNING --limit 10
animica quantum jobs list --offset 50 --json
```

### Submit Job
```bash
animica quantum jobs submit \
  --problem problem.json \
  --budget 1000 \
  --qubits 5 \
  --shots 1024
```

### Worker Operations
```bash
# Start worker
animica quantum contribute start --type quantum

# Stop worker
animica quantum contribute stop

# Force stop
animica quantum contribute stop --force

# Check worker status
animica quantum contribute status

# Monitor job
animica quantum contribute watch <job-id>
```

## Common Flags

All commands support:
- `--json` - Output as JSON
- `--rpc-url <URL>` - Override RPC URL

## Exit Codes
- `0` - Success
- `1` - Error

## Examples

### Submit and Monitor Job
```bash
# Submit job
JOB_ID=$(animica quantum jobs submit \
  --problem '{"circuit": "bell_state"}' \
  --budget 500 \
  --json | jq -r '.job_id')

# Monitor progress
animica quantum contribute watch $JOB_ID
```

### Check Credits and Submit
```bash
# Check available credits
animica quantum credits $(animica key address) --json | jq '.available'

# Submit job with budget
animica quantum jobs submit \
  --problem problem.json \
  --budget 1000
```

### Start Worker and Monitor
```bash
# Start quantum worker
animica quantum contribute start

# Check status
animica quantum contribute status --json

# Stop when done
animica quantum contribute stop
```

## RPC Methods

Backend RPC methods used by these commands:
- `aicf.getQuantumServiceStatus`
- `aicf.getQuantumCredits`
- `explorer_list_quantum_jobs`
- `aicf.submitQuantumJob`
- `aicf.startWorker`
- `aicf.stopWorker`
- `aicf.workerStatus`
- `aicf.getJobStatus`

## See Also
- `QUANTUM_CLI_IMPLEMENTATION.md` - Full implementation documentation
- `python/animica/cli/quantum.py` - Command implementation
- `python/animica/cli/quantum_contribute.py` - Contribution commands
