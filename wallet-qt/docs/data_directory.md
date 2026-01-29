# Data Directory Management

This document describes how the Animica Wallet manages data directories, wallets, and chain metadata.

## Overview

The wallet stores all data in a configurable **data directory**. By default, this uses OS-specific standard locations:

- **macOS**: `~/Library/Application Support/Animica/`
- **Windows**: `%APPDATA%\Animica\`
- **Linux**: `~/.animica/` (backward compatible with CLI)

## Directory Structure

```
<data_dir>/
├── wallets.json          # Wallet keystore (encrypted keys, addresses)
├── chain-1/              # Mainnet chain data
│   ├── db/              # Chain database
│   ├── headers/         # Block headers
│   └── state/           # State snapshots
├── chain-2/              # Testnet chain data
├── chain-1337/           # Devnet chain data
├── logs/                 # Node and wallet logs
│   ├── wallet.log
│   └── node-*.log
├── snapshots/            # Chain snapshots
└── .network_id           # Network marker file
```

## Changing Data Directory

### Via UI

1. Open Settings → Data Directory
2. Click "Choose Directory"
3. Select a folder
4. Wallet will restart with new directory

### Via Environment Variable

Set `ANIMICA_DATA_DIR` before launching the wallet:

```bash
export ANIMICA_DATA_DIR=/path/to/custom/dir
animica-wallet
```

### Priority Order

1. **Environment variable** (`ANIMICA_DATA_DIR`) - highest priority
2. **User-chosen path** (stored in QSettings)
3. **OS-specific default** - fallback

## Network Separation

The wallet prevents mixing data from different networks (mainnet, testnet, devnet).

### Network Marker

When you start a node, the wallet creates a `.network_id` file containing the network name:

```
devnet
```

### Mismatch Detection

If you try to start a different network in the same directory, the wallet will:

1. Detect the mismatch via `.network_id`
2. Block startup with an error message
3. Instruct you to choose a different data directory

**Example error:**

```
Network mismatch detected!

Data directory is configured for: devnet
You are trying to start: mainnet

Using the wrong network could corrupt your chain data.
Please choose a different data directory or switch to devnet.
```

## Wallet Import/Export

### Importing Wallets

The wallet can import `wallets.json` files from any location.

#### Import Steps

1. Select **Wallet → Import Wallets**
2. Choose a `wallets.json` file
3. If `wallets.json` already exists in your data directory:
   - **Replace**: Overwrites existing file (creates backup)
   - **Merge**: Combines with existing wallets (no duplicates)
   - **Cancel**: Abort import

#### Validation

The wallet validates imported files:
- Valid JSON structure
- Required fields present (address, public_key_hex, secret_key_hex)
- Algorithm IDs are recognized

#### Safety Features

- **Automatic backup**: Creates `wallets.json.bak.2026-01-29T12:34:56Z`
- **Atomic write**: Uses temp file + fsync + rename
- **Restrictive permissions**: chmod 0600 on Linux/macOS
- **Duplicate detection**: Merges by address (existing data preserved)

### Exporting Wallets

To export your wallets:

1. Select **Wallet → Export Wallets**
2. Choose destination folder/file
3. Wallet copies `wallets.json` to chosen location

**⚠️ Security Warning**: Exported files contain private keys. Store securely!

## Chain Metadata

### What is Stored

Chain metadata includes everything the node needs to resume from where it left off:

- **Chain database**: Block data, transactions, receipts
- **Headers**: Block headers and index
- **State**: World state snapshots
- **Peer store**: Known peers and reputation
- **Logs**: Historical node logs

### Preservation Guarantees

All chain metadata stays in the data directory under `chain-<id>/`:

- **Never separated**: Wallets and chain data live together
- **Network isolated**: Each chain ID has its own directory
- **Portable**: Move the entire data directory to another machine
- **Persistent**: Survives wallet restarts and upgrades

## Health Checks

The wallet performs health checks on startup:

### Data Directory Health

- ✅ Directory exists or can be created
- ✅ Directory is writable
- ✅ Required subdirectories exist
- ✅ Network marker matches (if present)

### Network Compatibility

- ✅ No network marker → any network allowed (first run)
- ✅ Matching network → proceed normally
- ❌ Mismatched network → block startup with error

## Backup Strategy

### Automatic Backups

The wallet creates automatic backups during:

- **Wallet import** (before merge/replace)
- Backup format: `wallets.json.bak.2026-01-29T12:34:56Z`

### Manual Backups

Recommended backup workflow:

1. Export `wallets.json` to secure location
2. Copy entire data directory for full backup:
   ```bash
   cp -r ~/.animica ~/backups/animica-2026-01-29
   ```

### Restore from Backup

1. Stop the wallet
2. Replace data directory or `wallets.json`
3. Restart wallet

## Troubleshooting

### "Data directory is not writable"

**Solution**: Check file permissions:

```bash
chmod 755 /path/to/data/dir
```

### "Network mismatch detected"

**Solution**: Either:
- Choose a different data directory
- Or switch to the network indicated in the error

### "Cannot import: invalid wallet structure"

**Solution**: Ensure `wallets.json` has required fields:
```json
{
  "version": 1,
  "wallets": [
    {
      "label": "my-wallet",
      "address": "anim1...",
      "alg_id": 4098,
      "alg_name": "dilithium3",
      "public_key_hex": "...",
      "secret_key_hex": "...",
      "created_at": "2026-01-29T00:00:00Z"
    }
  ]
}
```

### Lost wallet after changing data directory

**Solution**: The old data directory still exists. Either:
- Change back to the old directory
- Or import `wallets.json` from old directory to new one

## CLI Compatibility

The wallet does **not** change CLI behavior:

- CLI tools still default to `~/.animica/`
- CLI honors `ANIMICA_DATA_DIR` environment variable
- CLI can read/write wallets from any directory via `--wallet-file` flag

Example CLI usage:

```bash
# Use default directory
animica wallet list

# Use custom directory
ANIMICA_DATA_DIR=/custom/path animica wallet list

# Use explicit wallet file
animica wallet list --wallet-file /path/to/wallets.json
```

## Security Considerations

### File Permissions

On Linux/macOS, the wallet sets restrictive permissions:

- `wallets.json`: 0600 (owner read/write only)
- `.network_id`: 0600
- Directories: 0755 (owner full, others read/execute)

### Warnings

The wallet shows security warnings when:

- Importing wallets (contains private keys)
- Exporting wallets (data will be outside protected directory)
- Creating new wallets (reminder to backup)

### Best Practices

✅ **DO**:
- Backup `wallets.json` to encrypted storage
- Use full-disk encryption
- Verify file permissions after import
- Use separate data directories for different networks

❌ **DON'T**:
- Share `wallets.json` (contains private keys!)
- Use network drives for data directory (performance)
- Mix mainnet and testnet in same directory (prevented by wallet)
- Store backups in plain text on cloud storage

## Migration Guide

### From ~/.animica to Custom Directory

1. Stop wallet and node
2. Choose new directory in Settings
3. Import `~/.animica/wallets.json` to new directory
4. Copy chain data if desired:
   ```bash
   cp -r ~/.animica/chain-* /new/data/dir/
   ```
5. Restart wallet

### From CLI to Wallet

The wallet automatically finds `~/.animica/` on Linux (default).

For other platforms:
1. Export wallets from CLI:
   ```bash
   cp ~/.animica/wallets.json ~/Desktop/
   ```
2. Import via wallet UI

## Advanced Topics

### Custom Network IDs

If using custom network IDs (not 1, 2, or 1337):

1. Set `ANIMICA_CHAIN_ID` environment variable
2. Wallet will create `chain-<id>` directory
3. Network marker will use custom network name

### Multi-Network Workflow

To work with multiple networks simultaneously:

1. Create separate data directories:
   ```
   ~/animica-mainnet/
   ~/animica-testnet/
   ~/animica-devnet/
   ```

2. Launch wallet with appropriate directory:
   ```bash
   ANIMICA_DATA_DIR=~/animica-mainnet animica-wallet
   ```

3. Each instance has isolated wallets and chain data

### Shared Wallets, Separate Chains

To use same wallets across networks:

1. Export `wallets.json` from one directory
2. Import to other directories
3. Each directory maintains separate chain data

## API Reference

### DataDirManager

C++ class for managing data directories:

```cpp
DataDirManager manager;

// Get current directory
QString dataDir = manager.getDataDir();

// Set custom directory
manager.setDataDir("/custom/path");

// Get specific paths
QString walletsPath = manager.getWalletsFilePath();
QString chainDir = manager.getChainDataDir(1);  // mainnet

// Validate before using
QString errorMsg;
if (manager.validateDataDir("/path", errorMsg)) {
    // Valid
}

// Check network compatibility
if (!manager.checkNetworkCompatibility("mainnet", errorMsg)) {
    // Show error
}
```

### WalletImporter

C++ class for importing wallets:

```cpp
WalletImporter importer;

// Validate before importing
auto validation = importer.validateWalletFile("/path/to/wallets.json");
if (validation.valid) {
    // Import
    auto result = importer.importWallets(
        "/path/to/wallets.json",
        "/target/wallets.json",
        WalletImporter::ConflictResolution::Merge
    );
    
    if (result.success) {
        qDebug() << "Imported:" << result.walletsImported;
        qDebug() << "Backup at:" << result.backupPath;
    }
}
```

## Support

For issues related to data directories:

1. Check diagnostics: **Help → Diagnostics**
2. View logs: **Help → Open Logs Folder**
3. Report issue with diagnostic output

## See Also

- [Architecture](architecture.md) - Overall wallet design
- [Node Integration](node_integration_report.md) - How node uses data directory
- [Security](security.md) - Security best practices
