# Send/Receive Implementation Guide

This document describes the wallet send/receive functionality implementation.

## Architecture

### Backend (walletd)

The walletd service (`apps/qt-wallet-py/src/animica_qt_wallet/walletd/server.py`) implements the following transaction methods:

#### `tx.estimateFees`
Estimates transaction fees based on gas limit and current network conditions.

**Parameters:**
- `gas_limit` (int): Gas limit for the transaction (default: 21000)
- `base_fee` (int, optional): Base fee override
- `tip` (int): Miner tip (default: 0)

**Returns:**
```json
{
  "base_fee": 1000000000,
  "tip": 0,
  "max_fee": 1000000000,
  "estimated_total": 21000000000000
}
```

#### `tx.build`
Builds an unsigned transaction with automatic nonce retrieval.

**Parameters:**
- `from` (string): Sender address
- `to` (string | null): Recipient address (null for contract deployment)
- `value` (int): Amount in nANM (nano-ANM)
- `gas_limit` (int): Gas limit
- `max_fee` (int): Maximum fee per gas unit in nANM
- `nonce` (int, optional): Transaction nonce (auto-fetched if not provided)
- `data` (string): Transaction data (default: "")

**Returns:**
```json
{
  "from": "anim1...",
  "to": "anim1...",
  "value": 1000000000,
  "gas_limit": 21000,
  "max_fee": 1000000000,
  "nonce": 5,
  "chain_id": 1,
  "data": ""
}
```

#### `tx.sign`
Signs a transaction using a wallet account.

**Parameters:**
- `tx` (object): Transaction object from `tx.build`
- `from` (string): Sender address (must match an account in the wallet)

**Returns:**
```json
{
  "signed_tx": "0x...",
  "tx_hash": "0x..."
}
```

#### `tx.send`
Submits a signed transaction to the node.

**Parameters:**
- `signed_tx` (string): Signed transaction hex string from `tx.sign`

**Returns:**
```json
"0x..." // transaction hash
```

#### `tx.get`
Retrieves transaction details by hash (proxies to node RPC).

**Parameters:**
- `hash` (string): Transaction hash

**Returns:** Transaction object or null

### Frontend (UI)

#### Send Tab (`send_tab.py`)

**Features:**
- Account selector dropdown
- Recipient address input with validation
- Amount input (in ANM)
- Advanced options (collapsible):
  - Gas limit
  - Max fee
  - Custom nonce
  - Fee estimation
- Confirmation modal with full transaction details
- Success dialog with copyable transaction hash
- Error mapping for user-friendly messages

**Error Mapping:**
The `_map_error()` method translates technical errors into user-friendly messages:
- "Insufficient balance" → Clear balance error
- "Chain ID mismatch" → Network configuration issue
- "Signature verification failed" → Signature error
- "Node not running" → Start node instruction
- "Wallet locked" → Unlock wallet instruction

#### Receive Tab (`receive_tab.py`)

**Features:**
- Account selector
- Full address display (selectable text)
- Copy to clipboard button
- QR code generation (optional, requires `qrcode[pil]`)
- Visual feedback on copy action

## Flow Diagrams

### Send Transaction Flow

```
User fills form → Click "Send" → Validate inputs
                                       ↓
                              Build transaction (tx.build)
                                       ↓
                              Show confirmation dialog
                                       ↓
                        User confirms → Sign transaction (tx.sign)
                                       ↓
                              Send to network (tx.send)
                                       ↓
                              Show success with tx hash
```

### Error Handling Flow

```
Error occurs → Extract error message → Map to friendly message
                                            ↓
                              Display in UI + Optional QMessageBox
```

## Testing

### Validation Test

Run the validation test to ensure all components are in place:

```bash
cd apps/qt-wallet-py
python test_send_receive.py
```

This checks:
1. Walletd server methods
2. Walletd manager client methods
3. Send tab structure
4. Receive tab structure
5. Main window integration

### Manual Testing

1. **Start the wallet:**
   ```bash
   cd apps/qt-wallet-py
   ./run.sh
   ```

2. **Unlock wallet and create/import account**

3. **Start node** (required for sending):
   - Go to Node tab
   - Select network (mainnet/testnet)
   - Click "Start Node"

4. **Test Send:**
   - Go to Send tab
   - Select from account
   - Enter recipient address (e.g., `anim1...`)
   - Enter amount (e.g., `1.5` for 1.5 ANM)
   - (Optional) Adjust gas/fee in advanced section
   - Click "Send"
   - Review confirmation dialog
   - Confirm to send

5. **Test Receive:**
   - Go to Receive tab
   - Select account
   - Copy address or scan QR code

## Common Errors and Solutions

### "Node is not running"
**Solution:** Start the node from the Node tab before sending transactions.

### "Wallet is locked"
**Solution:** Unlock the wallet from the Accounts section.

### "Insufficient balance"
**Solution:** Ensure the sending account has enough ANM to cover both the transfer amount and gas fees.

### "Chain ID mismatch"
**Solution:** Ensure the node is running on the correct network (mainnet vs testnet).

### QR code not generating
**Solution:** Install qrcode library: `pip install qrcode[pil]`

## Security Considerations

1. **Private Key Storage**: Private keys are encrypted with AES-256-GCM using password-derived keys (Scrypt KDF)
2. **No Network Exposure**: Walletd only accepts local connections (127.0.0.1)
3. **Token Authentication**: All walletd RPC calls require Bearer token
4. **Confirmation Required**: All sends require explicit user confirmation
5. **Input Validation**: Address and amount validation before signing

## Future Enhancements

Potential improvements:
- Transaction history view
- Advanced fee estimation using network congestion
- Multi-signature support
- Hardware wallet integration
- Transaction batching
- Custom token support
