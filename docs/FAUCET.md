# Animica Faucet (Devnet/Testnet Only)

## Overview

The Animica faucet provides unlimited test funds for non-mainnet environments (devnet and testnet). It allows developers to quickly obtain test tokens without requiring block production or complex transaction flows.

## Important Security Restrictions

⚠️ **The faucet is ONLY available on non-mainnet networks:**
- ✅ Devnet (chainId: 1337)
- ✅ Testnet (chainId: 2)
- ❌ Mainnet (chainId: 1) - **EXPLICITLY BLOCKED**

Any attempt to use the faucet on mainnet will result in an error:
```json
{
  "error": {
    "code": -32600,
    "message": "Faucet is not available on mainnet",
    "data": {
      "chainId": 1,
      "reason": "mainnet_disabled"
    }
  }
}
```

## Features

- **Unlimited Supply**: No supply cap or rate limits on testnet/devnet
- **Direct Credit**: Funds are credited directly to the state DB (no block production required)
- **Default Amount**: 500,000,000 ANM (500,000,000,000,000,000 base units)
- **Custom Amounts**: Optional override parameter for custom amounts
- **Address Formats**: Accepts both bech32m (anim1...) and hex (0x...) addresses
- **Idempotent**: Can be called multiple times; balances accumulate

## Genesis Pre-funding

The following address is pre-funded with 500,000,000 ANM in devnet and testnet genesis files:

```
anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz
```

This provides immediate test funds without requiring the faucet for this specific address.

## RPC API

### Method: `faucet.request`

Request test funds from the faucet.

#### Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `address` | string | Yes | Recipient address (bech32m or hex format) |
| `amount` | integer | No | Amount in base units (default: 500000000000000000) |

#### Returns

```typescript
{
  address: string,      // recipient address (as provided)
  amount: string,       // amount credited (hex quantity)
  balance: string,      // new balance after credit (hex quantity)
  message: string       // confirmation message
}
```

#### Example Request (JSON-RPC)

Default amount (500M ANM):
```json
{
  "jsonrpc": "2.0",
  "method": "faucet.request",
  "params": {
    "address": "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
  },
  "id": 1
}
```

Custom amount (1M ANM = 1,000,000,000,000,000 base units):
```json
{
  "jsonrpc": "2.0",
  "method": "faucet.request",
  "params": {
    "address": "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
    "amount": 1000000000000000
  },
  "id": 1
}
```

#### Example Response

```json
{
  "jsonrpc": "2.0",
  "result": {
    "address": "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
    "amount": "0x6f05b59d3b20000",
    "balance": "0xde0b6b3a7640000",
    "message": "Credited 500000000000000000 base units to anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
  },
  "id": 1
}
```

## CLI Usage

### Command: `animica faucet request`

Request test funds from the faucet.

#### Syntax

```bash
animica faucet request ADDRESS [OPTIONS]
```

#### Arguments

- `ADDRESS` - Recipient address (bech32m anim1... or hex 0x...)

#### Options

- `--amount, -a` - Amount in base units (default: 500,000,000 ANM)
- `--json` - Output JSON format instead of human-readable text

#### Examples

Request default amount (500M ANM):
```bash
animica faucet request anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz
```

Output:
```
✓ Faucet request successful!
  Address:      anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz
  Credited:     500,000,000.0 ANM (500,000,000,000,000,000 base units)
  New balance:  1,000,000,000.0 ANM (1,000,000,000,000,000,000 base units)
```

Request custom amount (1M ANM):
```bash
animica faucet request anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz --amount 1000000000000000
```

Get JSON output:
```bash
animica faucet request anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz --json
```

### Network Configuration

Set `ANIMICA_RPC_URL` to point to your target network:

```bash
# Devnet (default)
export ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc

# Testnet
export ANIMICA_RPC_URL=https://testnet-127.0.0.1/rpc

# Mainnet (faucet will fail)
export ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc
```

## Token Decimals

ANM has 9 decimals:
- 1 ANM = 1,000,000,000 base units
- 500,000,000 ANM = 500,000,000,000,000,000 base units
- 1,000,000 ANM = 1,000,000,000,000,000 base units

## Error Handling

### Mainnet Error
```json
{
  "error": {
    "code": -32600,
    "message": "Faucet is not available on mainnet"
  }
}
```

### Invalid Address
```json
{
  "error": {
    "code": -32602,
    "message": "address must be anim… (bech32m) or 0x… (hex)"
  }
}
```

### Invalid Amount
```json
{
  "error": {
    "code": -32602,
    "message": "amount must be a positive integer"
  }
}
```

## Testing

Run the faucet tests:

```bash
# Genesis prefund tests
pytest tests/unit/test_genesis_prefund.py -v

# RPC faucet tests
pytest rpc/tests/test_faucet.py -v
```

## Implementation Details

### Architecture

1. **RPC Method** (`rpc/methods/faucet.py`):
   - Validates chain ID (rejects mainnet)
   - Parses and validates address
   - Credits state DB directly using `add_balance`
   - Returns balance information

2. **CLI Command** (`python/animica/cli/faucet.py`):
   - User-friendly interface
   - Calls RPC method via HTTP
   - Formats output for readability

3. **Genesis Files**:
   - `genesis/genesis.sample.devnet.json` - includes prefund
   - `genesis/genesis.sample.testnet.json` - includes prefund
   - `genesis/genesis.sample.mainnet.json` - NO prefund (unchanged)

### Security

- **Chain ID Check**: First operation in `faucet_request()` is to verify `chainId != 1`
- **No Bypass**: No configuration or parameter can override the mainnet block
- **State DB Only**: Faucet credits state DB directly; no transaction pool or mempool involved
- **No Rate Limits**: Intentionally unlimited for testnet/devnet simplicity

## FAQ

**Q: Why is the faucet unlimited on testnet/devnet?**  
A: Testnet and devnet are development environments where developers need frequent, flexible access to test funds. Rate limits would slow down development and testing workflows.

**Q: Can I use the faucet on mainnet?**  
A: No. The faucet is explicitly blocked on mainnet (chainId=1) to prevent any potential abuse or confusion about real token distribution.

**Q: How often can I request funds?**  
A: As often as needed on testnet/devnet. Each request adds to the existing balance.

**Q: What happens if I request more than the default amount?**  
A: You can specify any positive amount using the `amount` parameter. There are no caps on testnet/devnet.

**Q: Do I need to wait for blocks to be produced?**  
A: No. The faucet credits the state DB directly, so funds are available immediately without requiring block production.

## See Also

- [RPC API Documentation](../rpc/README.md)
- [CLI Documentation](../python/animica/cli/README.md)
- [Genesis Configuration](../genesis/README.md)
