# External RPC API Documentation

The Animica Wallet provides an external RPC interface that allows local applications to request wallet actions with user approval. This enables dApps and tools to interact with the wallet securely.

## Security Model

### Authentication
All requests must include the walletd authentication token:
- Token is stored in `~/.animica-wallet/walletd.token` (or platform-specific location)
- Use `Authorization: Bearer <token>` header or `X-Auth-Token: <token>` header

### Restrictions
1. **Localhost only**: Only connections from `127.0.0.1` or `::1` are accepted
2. **Token required**: All requests must include valid authentication token
3. **App allowlist**: Applications can be added to allowlist (default: deny)
4. **Rate limiting**: Default 10 requests/minute with burst of 5
5. **User approval**: All signing/sending operations require explicit approval in UI

## API Endpoints

### Base URL
```
http://127.0.0.1:17834/external
```

### Methods

#### `wallet_getChainId`
Get the current chain ID. Does not require approval.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "wallet_getChainId",
  "params": {}
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": 1
}
```

#### `wallet_requestAccounts`
Request access to wallet accounts. Requires user approval.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "wallet_requestAccounts",
  "params": {}
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    "anim1qyfeats5ck0ceh70xr7yfcdvmcyep5nwqxw8z",
    "anim1qzfx2kzxvzz0sqfqpfpqpqpqpqpqpqpqpqpq"
  ]
}
```

#### `wallet_signTransaction`
Sign a transaction. Requires user approval.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "wallet_signTransaction",
  "params": {
    "from": "anim1qyfeats5ck0ceh70xr7yfcdvmcyep5nwqxw8z",
    "transaction": {
      "from": "anim1qyfeats5ck0ceh70xr7yfcdvmcyep5nwqxw8z",
      "to": "anim1qzfx2kzxvzz0sqfqpfpqpqpqpqpqpqpqpqpq",
      "value": 1000000000000000000,
      "gas_limit": 21000,
      "max_fee": 1000000000,
      "data": ""
    }
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "signed_tx": "0x...",
    "tx_hash": "0x..."
  }
}
```

#### `wallet_sendTransaction`
Sign and send a transaction. Requires user approval.

**Request:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "wallet_sendTransaction",
  "params": {
    "from": "anim1qyfeats5ck0ceh70xr7yfcdvmcyep5nwqxw8z",
    "transaction": {
      "to": "anim1qzfx2kzxvzz0sqfqpfpqpqpqpqpqpqpqpqpq",
      "value": 1000000000000000000,
      "gas_limit": 21000,
      "max_fee": 1000000000,
      "data": ""
    }
  }
}
```

**Response:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": "0xabcd1234..." 
}
```

## Approval Flow

1. **Application makes request**: External app calls one of the approval-required methods
2. **Request queued**: Walletd creates an approval request in the queue
3. **UI notification**: Main window polls for pending approvals and displays dialog
4. **User decision**: User reviews details and approves/denies
5. **Response sent**: Application receives result or error
6. **Timeout**: If no response in 2 minutes, request times out

## Approval Dialog Details

When a request requires approval, the UI shows:
- **Requesting application**: Process name, PID, IP address
- **Method**: The operation being requested
- **Transaction details**: For signing/sending, shows from, to, value, gas, etc.
- **Warning**: Reminds user to only approve trusted applications

## Rate Limiting

Default rate limits:
- **10 requests per minute** per client
- **Burst size of 5** requests

Rate limit errors include:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": 500,
    "message": "Rate limit exceeded. Retry after 45.3s"
  }
}
```

## App Allowlist

Applications can be added to the allowlist to:
1. Allow access when default policy is "deny"
2. Enable auto-approval (skip approval dialog)

**Note**: Auto-approval should only be enabled for fully trusted applications.

## Error Codes

| Code | Message | Description |
|------|---------|-------------|
| 400  | Invalid JSON | Request body is not valid JSON |
| 401  | Unauthorized | Missing or invalid authentication token |
| 403  | Only localhost connections allowed | Request from non-localhost IP |
| 500  | Various | Runtime errors (wallet locked, approval denied, timeout, etc.) |

## Example Client

See `example_external_rpc.py` for a complete Python example showing:
- Token loading
- RPC calls with authentication
- Handling approval workflow
- Error handling

## Internal Methods (UI only)

These methods are used by the wallet UI to manage approvals:

### `approval.list`
List pending approval requests.

### `approval.respond`
Respond to an approval request with approve/deny.

**These methods should not be called by external applications.**
