# Mining Rewards Persistence - User Guide

## Problem

When you reset your Animica node, you lose all mining rewards and balances even though your wallet addresses are preserved.

**Why does this happen?**
- Wallet addresses are stored in: `~/.animica/wallets.json` ✅ (preserved)
- Account balances are stored in: `~/.animica/chain-{id}/animica.db` ❌ (deleted during reset)

## Solution

The node reset command now automatically exports your balances before deleting data, giving you a record of what you earned.

## Quick Start

### Standard Reset (with backup)

```bash
# Make sure your node is running first!
animica node up

# Reset will automatically backup balances
animica node reset

# View what balances you had
animica balance show
```

### Manual Balance Export

```bash
# Export anytime (not just during reset)
animica balance export

# View the backup
animica balance show

# Export to custom location
animica balance export --output ~/my-backups/balances-$(date +%Y%m%d).json
```

## Detailed Usage

### 1. Before Reset: Export Balances

**Option A: Automatic (during reset)**
```bash
animica node reset
# Balances are automatically exported if node is running
```

**Option B: Manual (before reset)**
```bash
animica balance export
# Creates: ~/.animica/chain-{chain_id}_balances_backup.json
```

### 2. Reset Your Node

```bash
# Standard reset (with balance backup)
animica node reset

# Skip balance backup (not recommended)
animica node reset --no-backup-balances

# Reset and restart immediately
animica node reset --up
```

### 3. After Reset: View Lost Balances

```bash
# View the backup
animica balance show

# Example output:
# Balance Backup: ~/.animica/chain-1337_balances_backup.json
# Exported: 2024-01-12T18:30:00Z
# Network data dir: /home/user/.animica/chain-1337
# RPC URL: http://127.0.0.1:8545/rpc
#
# Total addresses: 3
# Non-zero balances: 2
#
# Addresses with balances:
#   miner1               1.250000000 ANM  (anim1abc...)
#   rewards              0.500000000 ANM  (anim1def...)
```

## Command Reference

### `animica node reset`

Enhanced with balance backup functionality.

**Options:**
```bash
--backup-balances/--no-backup-balances
    Export wallet balances before reset (default: enabled)

--volumes/--no-volumes
    Remove docker volumes (default: enabled)

--host/--no-host
    Remove host data directories (default: enabled)

--yes, -y
    Run non-interactively (default: disabled)

--up/--no-up
    Start node after reset (default: disabled)
```

**Examples:**
```bash
# Standard reset with backup
animica node reset

# Reset without backup (dangerous!)
animica node reset --no-backup-balances

# Reset and restart immediately
animica node reset --yes --up

# Reset only host data (keep volumes)
animica node reset --no-volumes
```

### `animica balance export`

Export wallet balances to a backup file.

**Options:**
```bash
--network TEXT
    Network to export from (default: active network)

--rpc-url URL
    RPC endpoint (default: network default)

--wallet-file PATH
    Wallet file path (default: ~/.animica/wallets.json)

--output, -o PATH
    Output file for backup (default: auto-generated)
```

**Examples:**
```bash
# Export from active network
animica balance export

# Export from specific network
animica balance export --network testnet

# Export to custom location
animica balance export --output ~/backups/balances.json

# Export using custom RPC
animica balance export --rpc-url http://remote-node:8545/rpc
```

### `animica balance show`

Display contents of a balance backup file.

**Arguments:**
```bash
backup_file
    Path to backup file (default: latest for active network)
```

**Options:**
```bash
--network TEXT
    Network to show backup for (default: active network)
```

**Examples:**
```bash
# Show latest backup for active network
animica balance show

# Show specific backup file
animica balance show ~/backups/balances-20240112.json

# Show backup for specific network
animica balance show --network testnet
```

## Backup File Format

Balance backups are saved as JSON files:

```json
{
  "version": 1,
  "exported_at": "2024-01-12T18:30:00.123456+00:00",
  "data_dir": "/home/user/.animica/chain-1337",
  "rpc_url": "http://127.0.0.1:8545/rpc",
  "balances": [
    {
      "label": "miner1",
      "address": "anim1abc...",
      "hex_address": "0x1234...",
      "balance": 1250000000
    },
    {
      "label": "rewards",
      "address": "anim1def...",
      "hex_address": "0x5678...",
      "balance": 500000000
    }
  ]
}
```

**Notes:**
- Balance is in nano-ANM (1 ANM = 1,000,000,000 nANM)
- File is stored in your home directory (not in chain data)
- File survives node reset
- Contains sensitive information - protect accordingly

## Workflow Examples

### Scenario 1: Troubleshooting with Reset

```bash
# 1. Start node and mine some blocks
animica node up
animica miner mine-blocks --address miner1 --count 100

# 2. Check your balance
animica wallet show miner1
# Output: Balance: 10.5 ANM

# 3. Node has issues, need to reset
animica balance export  # Manual backup (optional, reset does this)
animica node reset --yes

# 4. View what you lost
animica balance show
# Shows: miner1 had 10.5 ANM

# 5. Start fresh and mine again
animica node up
animica miner mine-blocks --address miner1 --count 100
```

### Scenario 2: Regular Backups

```bash
# Setup automatic backups (cron job)
0 0 * * * animica balance export --output ~/backups/balances-$(date +\%Y\%m\%d).json

# Keep historical record of earnings
ls -l ~/backups/
# balances-20240101.json
# balances-20240102.json
# balances-20240103.json
```

### Scenario 3: Multi-Network Management

```bash
# Export balances from all networks
animica balance export --network mainnet --output ~/mainnet-balances.json
animica balance export --network testnet --output ~/testnet-balances.json
animica balance export --network devnet --output ~/devnet-balances.json

# View each backup
animica balance show ~/mainnet-balances.json
animica balance show ~/testnet-balances.json
animica balance show ~/devnet-balances.json
```

## Troubleshooting

### "Node is not running - cannot backup balances"

**Problem:** The node must be running to query balances.

**Solution:**
```bash
# Start the node first
animica node up

# Wait for it to be ready
animica node status

# Then export balances
animica balance export

# Now you can reset safely
animica node reset
```

### "Balance backup file not found"

**Problem:** No backup exists for the selected network.

**Solution:**
```bash
# Check which backups exist
ls ~/.animica/*_balances_backup.json

# Create a backup manually
animica balance export

# Or specify the exact file
animica balance show ~/path/to/backup.json
```

### "RPC call failed"

**Problem:** Cannot connect to RPC endpoint.

**Solution:**
```bash
# Check node is running
animica node status

# Verify RPC URL
echo $ANIMICA_RPC_URL

# Try different RPC URL
animica balance export --rpc-url http://127.0.0.1:8545/rpc
```

## Security Considerations

### Backup File Security

Balance backup files contain sensitive information:
- All wallet addresses
- Current balances
- Address labels

**Recommendations:**
1. Files are automatically secured (user-only permissions)
2. Don't share backup files publicly
3. Store backups securely (encrypted if sensitive)
4. Delete old backups when no longer needed

### No Automatic Restoration

Balance restoration is **intentionally not automatic** because:
1. Security: Prevents unauthorized balance manipulation
2. Integrity: Maintains blockchain state consistency
3. Audit: Forces users to understand what was lost

**To restore balances:**
- Mine new blocks to the same addresses
- The balances will naturally accumulate again
- Backup serves as a record, not a restoration tool

## FAQ

**Q: Will this restore my balances after reset?**
A: No. It only exports them for your records. You must mine again to earn new rewards.

**Q: Can I backup balances from a node that's not running?**
A: Not currently. The node must be accessible via RPC. Future versions may support direct DB reading.

**Q: Where are backup files stored?**
A: In your home directory: `~/.animica/chain-{chain_id}_balances_backup.json`

**Q: Can I disable automatic backup?**
A: Yes, use `--no-backup-balances` flag, but this is not recommended.

**Q: What if I forget to backup before reset?**
A: If the node is running when you run `reset`, it will automatically backup. If not, the balances are lost.

**Q: How do I restore balances from a backup?**
A: There's no automatic restore. Mine new blocks to the same addresses to earn rewards again.

**Q: Can I use this for disaster recovery?**
A: Yes, for reference. But you'll need to re-mine to actually restore the balances.

## Next Steps

1. **Start your node**: `animica node up`
2. **Mine some blocks**: `animica miner mine-blocks --address <your-wallet> --count 10`
3. **Export balances**: `animica balance export`
4. **View the backup**: `animica balance show`
5. **Reset safely**: `animica node reset` (balances automatically backed up)

For more information:
- Run `animica balance --help`
- Run `animica node reset --help`
- See: `MINING_REWARDS_PERSISTENCE_FIX.md`
