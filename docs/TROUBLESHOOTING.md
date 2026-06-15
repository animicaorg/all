# Troubleshooting Guide

This guide provides solutions to common issues with Animica, ENA, and AICF.

## Quick Diagnostics

Run the doctor commands first to identify issues:

```bash
# Check node configuration
animica node doctor

# Check ENA setup
animica ena doctor

# Check AICF setup
animica ena aicf doctor
```

These commands will identify common misconfigurations and suggest exact fixes.

## Common Issues

### Node & RPC Issues

#### "RPC not reachable"

**Symptoms**:
- Commands timeout or fail
- `animica rpc call` returns connection error

**Diagnosis**:
```bash
animica node doctor
```

**Fixes**:

1. **Node not running**:
   ```bash
   # Start the node
   animica node up
   
   # Check status
   animica node status
   ```

2. **Wrong RPC URL**:
   ```bash
   # Test connectivity
   curl http://127.0.0.1:8545/rpc -X POST \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"chain.getChainId","params":[],"id":1}'
   
   # Set correct URL
   export ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc
   ```

3. **Firewall blocking**:
   ```bash
   # Check if port is open
   netstat -an | grep 8545
   
   # Allow port (Linux)
   sudo ufw allow 8545
   ```

#### "Data directory not writable"

**Symptoms**:
- Node fails to start
- Database errors
- "Permission denied" errors

**Diagnosis**:
```bash
animica node doctor --data-dir ~/.animica/data
```

**Fixes**:

1. **Fix permissions**:
   ```bash
   # Make directory writable
   chmod 755 ~/.animica
   chmod 755 ~/.animica/data
   
   # Fix ownership (if needed)
   sudo chown -R $USER:$USER ~/.animica
   ```

2. **SELinux preventing writes** (Linux):
   ```bash
   # Check SELinux status
   getenforce
   
   # Temporarily disable (not recommended for production)
   sudo setenforce 0
   
   # Or add SELinux policy
   sudo chcon -R -t user_home_t ~/.animica
   ```

3. **Use different directory**:
   ```bash
   # Start node with custom data dir
   animica node up --data-dir /var/lib/animica
   ```

#### "Mempool/DB always read-only"

**Symptoms**:
- Transactions not accepted
- "Database is locked" errors
- Mempool not updating

**Diagnosis**:
```bash
# Check if data dir is writable
animica node doctor

# Check disk space
df -h ~/.animica
```

**Fixes**:

1. **Insufficient disk space**:
   ```bash
   # Free up space
   rm -rf ~/.animica/logs/*.old
   
   # Or use different partition
   animica node up --data-dir /mnt/storage/animica
   ```

2. **Docker volume not persistent**:
   ```yaml
   # In docker-compose.yml, ensure volumes are mounted:
   services:
     node:
       volumes:
         - ./data:/var/lib/animica:rw  # Note: :rw for read-write
   ```

3. **Multiple processes accessing DB**:
   ```bash
   # Stop all instances
   animica node down
   pkill -f animica
   
   # Restart single instance
   animica node up
   ```

### Wallet Issues

#### "Wallet file not found"

**Symptoms**:
- `Error: Wallet file not found: ~/.animica/wallets.json`

**Fix**:
```bash
# Create new wallet
animica wallet new

# Or import existing
animica wallet import --file backup.json
```

#### "Balances show same on all wallets"

This issue should be fixed in the latest version. If you still see it:

**Diagnosis**:
```bash
# Check each wallet separately
animica wallet list
animica wallet balance --address anim1wallet1...
animica wallet balance --address anim1wallet2...
```

**Temporary workaround**:
```bash
# Clear cache and retry
rm -rf ~/.animica/cache
animica wallet balance --address <your_address>
```

**Fix**:
Update to latest version:
```bash
pip install -U animica
```

#### "Signature scheme disabled by policy"

**Symptoms**:
- Transaction fails with "signature scheme not allowed"
- `animica tx send` returns policy error

**Diagnosis**:
```bash
# Check active policy
animica rpc call chain.getParams | grep -A 10 pqAlgPolicy

# Check your wallet's signature scheme
animica wallet list --json | jq '.[].algId'
```

**Fixes**:

1. **Create wallet with allowed scheme**:
   ```bash
   # Check allowed schemes
   animica rpc call pq.getPolicy
   
   # Create wallet with allowed scheme (e.g., dilithium3)
   animica wallet new --alg dilithium3
   ```

2. **Update policy** (operators only):
   ```bash
   # WARNING: This requires governance approval
   # Edit spec/params.yaml or governance config
   # Add your scheme to allowed list
   # Deploy updated policy
   ```

3. **Use different network**:
   ```bash
   # Devnet may have different policies
   animica --network devnet tx send ...
   ```

### Transaction Issues

#### "Insufficient balance"

**Symptoms**:
- `Error: Insufficient balance for transaction`

**Diagnosis**:
```bash
# Check balance
animica wallet balance

# Check pending transactions
animica tx list --pending
```

**Fixes**:

1. **Get funds** (testnet/devnet):
   ```bash
   animica faucet request
   ```

2. **Wait for pending txs**:
   ```bash
   # Check status of pending txs
   animica tx status <hash>
   
   # Wait for confirmation
   ```

3. **Adjust amount**:
   ```bash
   # Send smaller amount, accounting for fees
   animica tx send --to <addr> --value 0.9 --gas 100000
   ```

#### "BigInt serialization error"

**Symptoms**:
- `TypeError: Do not know how to serialize a BigInt`
- JSON errors in wallet or CLI

**This should be fixed in latest version.**

**Immediate workaround**:
```bash
# Use string amounts instead of BigInt
animica tx send --value "1000000000" --to <addr>
```

**Permanent fix**:
```bash
# Update to latest version
pip install -U animica

# For wallet extension:
cd apps/wallet-extension
pnpm install
pnpm build
```

#### "Transaction stuck in mempool"

**Symptoms**:
- Transaction shows "pending" for long time
- Not included in blocks

**Diagnosis**:
```bash
# Check mempool
animica mempool list

# Check transaction status
animica tx status <hash>

# Check if you're synced
animica chain head
```

**Fixes**:

1. **Wait for next block**:
   ```bash
   # Check block time
   animica chain head
   
   # Wait ~60s for next block
   ```

2. **Increase gas price** (future):
   ```bash
   # Resubmit with higher gas
   animica tx send --gas-price 2 ...
   ```

3. **Check nonce**:
   ```bash
   # Verify nonce is sequential
   animica wallet nonce
   
   # If stuck, clear pending
   animica tx clear-pending
   ```

### ENA Issues

#### "ENA endpoint not reachable"

**Diagnosis**:
```bash
animica ena doctor

# Manual test
curl https://pool.animica.org/v1/models
```

**Fixes**:

1. **Network issue**:
   ```bash
   # Test connectivity
   ping pool.animica.org
   
   # Try with different network
   ```

2. **Use backup endpoint**:
   ```bash
   animica ena infer "Test" --endpoint https://backup.ena.org
   ```

3. **Check firewall**:
   ```bash
   # Allow outbound HTTPS
   sudo ufw allow out 443
   ```

#### "Payment transaction failed"

**Symptoms**:
- ENA payment fails
- AICF contribution not recorded

**Diagnosis**:
```bash
# Check wallet balance
animica wallet balance

# Check AICF address
animica ena pricing | grep AICF

# Verify transaction
animica tx status <hash>
```

**Fixes**:

1. **Insufficient balance**:
   ```bash
   # Get funds
   animica faucet request
   
   # Try with smaller max_tokens
   animica ena infer "Test" --max-tokens 50
   ```

2. **Wrong AICF address**:
   ```bash
   # Verify AICF address from pricing
   animica ena pricing
   
   # Update endpoint if needed
   animica ena infer "Test" --endpoint https://mainnet.ena.org
   ```

### AICF Issues

#### "Worker registration failed"

**Symptoms**:
- `animica ena aicf worker-register` fails

**Diagnosis**:
```bash
animica ena aicf doctor

# Check endpoint
curl https://pool.animica.org/v1/aicf/status
```

**Fixes**:

1. **Invalid address format**:
   ```bash
   # Ensure address starts with anim1
   animica wallet list
   
   # Use correct address
   animica ena aicf worker-register anim1... --name "MyWorker"
   ```

2. **Endpoint not supporting AICF**:
   ```bash
   # Use mainnet endpoint
   animica ena aicf worker-register <addr> \
     --endpoint https://pool.animica.org
   ```

#### "No jobs available"

**Symptoms**:
- Worker runs but gets no jobs

**Diagnosis**:
```bash
# Check worker status
animica ena aicf worker-status <worker_id>

# Check coordinator
animica ena aicf protocol-status
```

**Fixes**:

1. **Coordinator not running**:
   ```bash
   # Start coordinator (operators)
   animica aicf coordinator start
   ```

2. **No job demand**:
   - Wait for inference calls to create jobs
   - Jobs are created on-demand

3. **Worker not active**:
   ```bash
   # Re-register if needed
   animica ena aicf worker-register <addr> --name "MyWorker"
   ```

#### "Epoch not finalized"

**Symptoms**:
- Cannot claim rewards
- `animica ena aicf worker-claim` fails

**Diagnosis**:
```bash
# Check epoch status
animica ena aicf epoch-info <epoch_number>
```

**Fixes**:

1. **Wait for finalization**:
   - Epochs auto-finalize after challenge window
   - Default: 100 blocks after epoch end

2. **Manual finalization** (operators):
   ```bash
   animica aicf epoch finalize
   ```

## Debug Logging

Enable verbose logging for more details:

```bash
# Node logs
export RUST_LOG=debug
animica node up

# CLI verbose mode
animica --verbose wallet balance

# Wallet extension
# In browser console:
localStorage.setItem('DEBUG', 'animica:*')
```

## Getting Help

If issues persist:

1. **Run all doctor commands**:
   ```bash
   animica node doctor --json > node-diag.json
   animica ena doctor --json > ena-diag.json
   animica ena aicf doctor --json > aicf-diag.json
   ```

2. **Collect logs**:
   ```bash
   animica node logs --tail 100 > node-logs.txt
   ```

3. **Report issue** with:
   - Doctor outputs
   - Exact command that failed
   - Error message
   - OS and version
   - Animica version: `animica --version`

## System Requirements

### Minimum

- **OS**: Linux, macOS, Windows (WSL2)
- **RAM**: 4 GB
- **Disk**: 10 GB free
- **Network**: Stable internet connection

### Recommended

- **OS**: Ubuntu 22.04 LTS or later
- **RAM**: 8 GB
- **Disk**: 50 GB SSD
- **Network**: 10 Mbps+ symmetric

### For GPU Workers

- **GPU**: NVIDIA GPU with CUDA support
- **VRAM**: 8 GB minimum
- **Drivers**: Latest NVIDIA drivers + CUDA toolkit

## Performance Tips

1. **Use SSD for data directory**:
   ```bash
   animica node up --data-dir /mnt/ssd/animica
   ```

2. **Allocate more RAM** (Docker):
   ```yaml
   services:
     node:
       deploy:
         resources:
           limits:
             memory: 8G
   ```

3. **Optimize database**:
   ```bash
   # Periodic vacuum (when node is stopped)
   sqlite3 ~/.animica/data/state.db "VACUUM;"
   ```

4. **Use local RPC**:
   ```bash
   # Faster than remote RPC
   export ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc
   ```

## Further Reading

- [AICF Documentation](./AICF.md)
- [ENA Documentation](./ENA.md)
- [Node Setup Guide](./MULTI_NODE_DOCKER_SETUP.md)
- [Mining Troubleshooting](./MINING_TROUBLESHOOTING.md)
