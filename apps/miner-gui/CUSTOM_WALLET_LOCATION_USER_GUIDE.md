# Using Custom Wallet Locations - User Guide

## Quick Start

If your wallet is stored in a custom location (not `~/.animica/wallets.json`), you can now configure the miner GUI to use it.

## Method 1: During Initial Setup (Recommended)

1. **Start the Animica Miner GUI**
   - Run the application for the first time
   - The setup wizard will appear

2. **Configure Network**
   - Select your network (Mainnet, Testnet, or Devnet)
   - Test the RPC connection

3. **Import Your Wallet**
   - On the "Payout Address" page, click **"Import from Wallets"**
   - A file browser will open
   - Navigate to your custom wallet location (e.g., USB drive, custom folder)
   - Select your `wallets.json` file
   - Choose which address to use if multiple wallets exist

4. **Complete Setup**
   - Configure devices and performance
   - Click "Finish"
   - Your custom wallet location will be saved and used for mining

## Method 2: Manual Configuration Edit

If you already completed setup, you can manually edit the configuration:

1. **Open Configuration File**
   ```bash
   # macOS/Linux
   nano ~/.animica/gui-miner/config.json
   
   # Windows (WSL)
   nano ~/.animica/gui-miner/config.json
   ```

2. **Add Wallet File Location**
   ```json
   {
     "version": "1.0",
     "miner": {
       "payout_address": "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
       "wallet_file": "/Users/admin/Documents/my-wallets.json",
       "auto_start": false
     }
   }
   ```

3. **Save and Restart**
   - Save the file
   - Restart the miner GUI
   - Mining will now use your custom wallet location

## Method 3: Using Environment Variable

For advanced users or automation:

```bash
# Set before launching GUI
export ANIMICA_WALLETS_FILE=/custom/location/wallets.json

# Launch GUI
./animica-miner-gui
```

## Troubleshooting

### Error: "Address not found in wallet file"

**Solution 1:** Verify your wallet file contains the payout address:
```bash
cat /path/to/your/wallets.json | grep "anim1zqqjt..."
```

**Solution 2:** Re-import your wallet from the Configuration tab:
1. Open Configuration tab
2. Edit the JSON config
3. Update the `wallet_file` path
4. Save changes

### Error: "Mining failed with Invalid params"

This error occurs when the miner can't find your wallet file. Check:

1. **File Exists:**
   ```bash
   ls -la /path/to/your/wallets.json
   ```

2. **Correct Path in Config:**
   ```bash
   cat ~/.animica/gui-miner/config.json | grep wallet_file
   ```

3. **File Permissions:**
   ```bash
   chmod 600 /path/to/your/wallets.json
   ```

### Wallet on USB Drive or Network Share

**USB Drive:**
- Use absolute path: `/Volumes/MyUSB/wallets.json` (macOS) or `/media/usb/wallets.json` (Linux)
- Ensure drive is mounted before starting mining
- Consider copying wallet to local drive for reliability

**Network Share:**
- Ensure stable connection
- Use absolute path
- Consider latency impact on performance

## Priority Order

The miner GUI checks wallet locations in this order:

1. **Config file** (`~/.animica/gui-miner/config.json` → `miner.wallet_file`)
2. **Environment variable** (`ANIMICA_WALLETS_FILE`)
3. **Default location** (`~/.animica/wallets.json`)

## Security Best Practices

1. **Protect Your Wallet File:**
   ```bash
   chmod 600 /path/to/wallets.json
   ```

2. **Backup Regularly:**
   ```bash
   cp /path/to/wallets.json /path/to/backup/wallets-$(date +%Y%m%d).json
   ```

3. **Use Secure Locations:**
   - Encrypted drives preferred
   - Avoid cloud sync for security
   - Keep private keys secure

## Examples

### Example 1: USB Drive (macOS)
```json
{
  "miner": {
    "payout_address": "anim1...",
    "wallet_file": "/Volumes/MyUSB/animica-wallets.json"
  }
}
```

### Example 2: External Drive (Linux)
```json
{
  "miner": {
    "payout_address": "anim1...",
    "wallet_file": "/mnt/external/wallets.json"
  }
}
```

### Example 3: Custom Documents Folder
```json
{
  "miner": {
    "payout_address": "anim1...",
    "wallet_file": "/Users/admin/Documents/Animica/wallets.json"
  }
}
```

## Need Help?

- Check logs: Configuration tab → View logs
- Review configuration: Configuration tab → Validate
- Ask for help: [Community Discord/Forum]

## Related Documentation

- [Wallet CLI Guide](../../python/animica/cli/README.md)
- [Security Best Practices](../../SECURITY.md)
- [Miner GUI Documentation](README.md)
