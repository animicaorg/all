# Transaction Submission RPC Guide

## Node RPC Signature

The Animica node defines the transaction submission RPC method in `rpc/methods/tx.py`:

```python
@method(
    "tx.sendRawTransaction",
    desc="Submit a signed CBOR-encoded transaction. Param: rawTx (hex string '0x…'). Returns tx hash."
)
def tx_send_raw_transaction(rawTx: str) -> t.Any:
    # Implementation details...
```

### Parameter Binding

The RPC dispatcher (`rpc/jsonrpc.py`) uses Python's `inspect.signature` to bind parameters. It supports **two forms** of parameter passing:

#### Array Form (Positional)
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tx.sendRawTransaction",
  "params": ["0xabcdef..."]
}
```
- The array `["0xabcdef1234567890"]` binds to the positional parameter `rawTx`
- Valid per Python's `sig.bind_partial(*args_obj)`

#### Object Form (Keyword)
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tx.sendRawTransaction",
  "params": {
    "rawTx": "0xabcdef..."
  }
}
```
- The object `{ rawTx: "0xabcdef1234567890" }` binds to the keyword parameter `rawTx`
- Valid per Python's `sig.bind_partial(**args_obj)`

**Both forms are valid and equivalent.** The wallet extension primarily uses the object form for explicitness and type safety.

## Wallet Extension Implementation

### RpcClient.sendRawTransaction()

Location: `src/core/rpc/client.ts`

```typescript
async sendRawTransaction(rawTx: string): Promise<string> {
  // Uses OBJECT form with validation
  const params = { rawTx };
  validateSendRawTransactionParams(params);
  return this.call('tx.sendRawTransaction', params);
}
```

**Validation includes:**
- `params` must be an object (not array)
- `params.rawTx` must be a string
- `params.rawTx` must match `/^0x[0-9a-f]+$/i` (hex with 0x prefix)

### submitTransaction() Helper

Location: `src/tx/rpc.ts`

```typescript
export async function submitTransaction(
  rawTx: string,
  rpcCall: (method: string, params: any[]) => Promise<any>
): Promise<string> {
  // Uses ARRAY form (legacy compatibility)
  const result = await rpcCall('tx.sendRawTransaction', [rawTx]);
  return result;
}
```

This helper uses array form because its `rpcCall` signature expects `params: any[]`. Both forms work correctly with the node.

## Common Mistakes That Cause -32602 Errors

### Error: Invalid params (code -32602)

This error means the JSON-RPC `params` shape doesn't match what the method expects. Common causes:

#### 1. Double-Wrapped Params
```json
// ❌ WRONG
{
  "method": "tx.sendRawTransaction",
  "params": {
    "params": ["0xabcdef1234567890"]
  }
}
```
The node sees `params.params` which doesn't bind to `rawTx`.

#### 2. Wrong Key Name
```json
// ❌ WRONG
{
  "method": "tx.sendRawTransaction",
  "params": {
    "tx": "0xabcdef1234567890",
    "transaction": "0xabcdef1234567890"
  }
}
```
The node expects key `rawTx`, not `tx` or `transaction`.

#### 3. Missing 0x Prefix
```json
// ❌ WRONG
{
  "method": "tx.sendRawTransaction",
  "params": {
    "rawTx": "abcdef"
  }
}
```
While this passes JSON-RPC validation, the node's CBOR decoder expects `0x` prefix.

#### 4. Non-String Value
```json
// ❌ WRONG
{
  "method": "tx.sendRawTransaction",
  "params": {
    "rawTx": ["0xabcdef1234567890"]
  }
}
```
The node expects `rawTx: str`, not `rawTx: list`.

#### 5. Empty or Null
```json
// ❌ WRONG
{
  "method": "tx.sendRawTransaction",
  "params": {
    "rawTx": ""
  }
}
// ❌ WRONG
{
  "method": "tx.sendRawTransaction",
  "params": {
    "rawTx": null
  }
}
```

## Debug Logging

### Always-On Logging

The wallet extension **always logs -32602 errors** with full request details:

```javascript
console.error('[wallet-rpc] RPC ERROR', {
  url: 'http://...',
  method: 'tx.sendRawTransaction',
  fullRequest: {
    jsonrpc: '2.0',
    id: 123,
    method: 'tx.sendRawTransaction',
    params: { rawTx: '0x...' }
  },
  responseError: {
    code: -32602,
    message: 'Invalid params',
    data: '...'
  },
  hint: 'Code -32602 means params shape/type mismatch. Check the params object matches node signature.'
});
```

### Enable Full Debug Logging

**Build-time (requires rebuild):**
```bash
VITE_DEBUG_RPC_PAYLOADS=1 pnpm build
```

**Runtime (in service worker console):**
```javascript
globalThis.__ANIMICA_DEBUG_RPC_PAYLOADS__ = true
```

With full debug enabled, you'll see:
- Every RPC request before sending
- Every RPC response (success and error)
- Network errors (timeout, connection failed, etc.)

## Testing

Run the test suite to verify request shape correctness:

```bash
cd apps/wallet-extension
pnpm test rpc-request-shape.test.ts
```

Tests cover:
- ✅ Object form request building
- ✅ Array form request building
- ✅ Hex string validation
- ✅ 0x prefix validation
- ✅ Empty string rejection
- ✅ Common mistakes documentation

## Node Error Codes Reference

| Code    | Name                   | Cause                                      |
|---------|------------------------|--------------------------------------------|
| -32600  | Invalid Request        | Malformed JSON or missing required fields  |
| -32601  | Method not found       | Unknown method name                        |
| -32602  | Invalid params         | Params shape/type mismatch                 |
| -32603  | Internal error         | Node-side exception                        |
| -32012  | Signature error        | PQ signature verification failed           |
| -32000  | Transaction error      | Mempool rejection (nonce, balance, etc.)   |

**Note:** -32602 is specifically about JSON-RPC parameter binding, not transaction validity. Transaction errors use different codes.

## Migration Notes

If you're migrating from an older wallet version:

1. **Check params shape**: Ensure you're not double-wrapping params
2. **Hex format**: All transaction data must use `0x` prefix
3. **Type safety**: Use TypeScript types from `@animica/wallet-extension`
4. **Error handling**: Catch `RpcResponseError` which includes `code` and `data`

## Further Reading

- Node RPC implementation: `rpc/methods/tx.py`
- Dispatcher logic: `rpc/jsonrpc.py`
- Extension RPC client: `src/core/rpc/client.ts`
- Transaction building: `src/tx/index.ts`
