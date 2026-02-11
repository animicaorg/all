# Chain Reset Guide (AICF Version)

## Overview

A **chain reset** reinitializes the blockchain to genesis state (height 0) with a new genesis configuration. This guide covers the AICF-enabled chain reset that introduces the AI Compute Fund.

**⚠️ WARNING**: Chain reset deletes all blockchain data. Transactions, balances, and state are lost. Use only when intentional.

## Quick Start

### Hard Reset (Recommended)

Completely reset chain to fresh genesis with AICF:

```bash
# Stop node, delete all data, reset to genesis
animica node reset --hard --yes
```

### Step-by-Step Reset

```bash
# 1. Stop the node
animica node down

# 2. Reset chain (with confirmation)
animica node reset --hard

# 3. Start node with new genesis
animica node up

# 4. Verify new genesis
animica chain head
animica aicf status  # Check AICF pool initialized
```

## What Gets Reset

### Hard Reset (`--hard`)

Deletes:
- ✅ Chain database (`animica.db`)
- ✅ All blocks and transactions
- ✅ All account balances (except genesis allocations)
- ✅ All state data
- ✅ AICF pool state (resets to balance=0)
- ✅ Database journals and WAL files

Preserves:
- ⚠️ Wallet keys (in separate keystore)
- ⚠️ Configuration files
- ⚠️ Logs (unless manually deleted)

## Genesis Changes with AICF

### Before (Old Genesis)

```json
{
  "chainId": 1,
  "alloc": [
    {
      "address": "anim1foundation...",
      "balance": "81000000000000000"
    }
  ]
}
```

### After (New Genesis with AICF)

The key difference: AICF pool is initialized but NOT premined.

```json
{
  "chainId": 1,
  "genesisTime": "2026-02-11T00:00:00Z",
  "alloc": [
    {
      "address": "anim1foundation...",
      "balance": "81000000000000000"
    }
  ]
}
```

**AICF pool state (in StateDB, not alloc)**:
```json
{
  "balance": 0,                    // Starts at ZERO
  "cap": 119000000000000000,       // 119M ANM cap
  "issued_total": 0,
  "spent_total": 0
}
```

**Key differences**:
1. AICF pool starts at **balance = 0** (not premined)
2. Cap set to **119M ANM** (in base units)
3. Pool fills via AICF mining (not allocation)
4. No bech32 address owns the 119M ANM

## Chain ID Preservation

**Goal**: Maintain `chain_id = 1` for mainnet to avoid unnecessary incompatibility.

**How it works**:
1. Genesis file specifies `chainId: 1`
2. All nodes use same genesis file
3. Chain ID embedded in transactions for replay protection
4. Reset updates genesis **timestamp** and **state root** but keeps `chainId`

**Result**: 
- Old chain: `chainId=1`, genesis hash A
- New chain: `chainId=1`, genesis hash B
- Transactions from old chain invalid on new chain (different genesis)

## Operational Steps

### 1. Backup (if needed)

```bash
# Backup wallet keys (important!)
cp -r ~/.animica/keystore ~/backup/keystore

# Backup configuration
cp ~/.env ~/backup/.env

# Backup database (if you need to revert)
cp ~/.animica/animica.db ~/backup/animica.db.backup
```

### 2. Stop All Services

```bash
# Stop node
animica node down

# Stop miner (if running)
pkill -f "animica miner"

# Stop any AICF miners
pkill -f "animica aicf miner"

# Verify nothing is running
ps aux | grep animica
```

### 3. Perform Reset

```bash
# Interactive (asks for confirmation)
animica node reset --hard

# Non-interactive (auto-confirm)
animica node reset --hard --yes
```

**Output**:
```
⚠️  CHAIN RESET WARNING ⚠️
Network: mainnet
Data dir: /home/user/.animica
DB path: /home/user/.animica/animica.db

🔥 HARD RESET will delete:
  - Chain database: /home/user/.animica/animica.db
  - All blocks and transactions
  - All state data
  - AICF pool state

⚠️  This action CANNOT be undone!

Are you sure you want to proceed? [y/N]: y

📍 Stopping node if running...
   Stopping node process (PID 12345)...

🗑️  Deleting chain database...
   ✅ Deleted: /home/user/.animica/animica.db

✅ Chain reset complete!

Next steps:
  1. Start node: animica node up
  2. Node will initialize with new genesis
  3. AICF pool starts at balance=0
  4. Use 'animica aicf status' to check AICF state
```

### 4. Start Node with New Genesis

```bash
# Start node
animica node up

# Wait for node to initialize
sleep 5

# Check status
animica node status
```

**Expected**:
- Height: 0 (genesis)
- New genesis hash
- AICF pool: balance=0, cap=119M ANM

### 5. Verify New State

```bash
# Check chain head
animica chain head

# Check AICF pool
animica aicf status

# Should show:
# Balance:      0.00 ANM
# Capacity:     119,000,000.00 ANM
# Filled:       0.00%
```

### 6. Resume Operations

```bash
# Start AICF miner (fills the pool)
animica aicf miner \
  --address anim1your-address... \
  --difficulty 20 \
  --interval 60

# Start regular miner
animica miner mine-blocks \
  --address anim1your-address...
```

## Network-Specific Reset

### Mainnet

```bash
export ANIMICA_NETWORK=mainnet
animica node reset --hard --yes
```

### Testnet

```bash
export ANIMICA_NETWORK=testnet
animica node reset --hard --yes
```

### Devnet

```bash
export ANIMICA_NETWORK=devnet
animica node reset --hard --yes
```

## Best Practices

### Before Reset

- ✅ **Backup wallet keys** - Store safely offline
- ✅ **Coordinate with network** - Ensure all nodes reset together
- ✅ **Update genesis timestamp** - All nodes use same genesis file

### During Reset

- ✅ **Stop all services** - Node, miners, indexers
- ✅ **Delete completely** - Use `--hard` for clean slate
- ✅ **Verify deletion** - Check DB files gone

### After Reset

- ✅ **Verify genesis** - Check hash and height
- ✅ **Check AICF state** - Pool should be at 0
- ✅ **Test functionality** - Send test tx, mine block
- ✅ **Start AICF miner** - Begin filling the pool

## Summary

Chain reset workflow:

1. **Backup** wallet keys
2. **Stop** all services
3. **Reset** with `animica node reset --hard --yes`
4. **Start** node
5. **Verify** genesis and AICF state
6. **Start AICF miner** to fill pool

Key points:
- ✅ AICF pool starts at 0 (not premined)
- ✅ Chain ID preserved (chainId=1)
- ✅ Deterministic genesis (all nodes agree)
- ✅ 119M ANM earns into existence via AICF mining

For details, see:
- `docs/AICF.md` - Complete AICF guide
- `animica node reset --help` - CLI help
- `animica aicf --help` - AICF commands
