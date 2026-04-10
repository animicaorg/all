# Wallet CLI Test Fixtures

This directory contains example wallet store files for testing the wallet CLI.

## example_wallets.json

A sample wallet store file compatible with `~/.animica/wallets.json` format, containing:

- **premine**: Example premine wallet entry (marked as default)
- **alice**: Test user wallet
- **bob**: Test user wallet

### Format

The wallet store follows this schema:

```json
{
  "version": 1,
  "wallets": [
    {
      "label": "string",
      "address": "bech32 anim1... address",
      "alg_id": 4098,
      "alg_name": "sphincs_shake_128s",
      "public_key_hex": "hex string",
      "secret_key_hex": "hex string",
      "created_at": "ISO 8601 timestamp"
    }
  ],
  "default_address": "optional default address"
}
```

### Usage

These fixtures are used in tests to verify:
- Wallet lookup by address, label, or public key hex
- Default wallet store path resolution
- Environment variable and CLI flag overrides
- Backward compatibility with `--address` option

**Note**: The keys in these fixtures are for testing only and should NEVER be used on mainnet.
