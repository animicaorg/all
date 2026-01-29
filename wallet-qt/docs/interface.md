# RPC Interface Documentation

## Overview

This document describes the JSON-RPC interface between the Qt wallet and the embedded Animica node. The wallet communicates with the node over HTTP and WebSocket on `127.0.0.1:<port>`.

## Connection Details

- **Protocol**: JSON-RPC 2.0
- **Transport**: HTTP POST (primary), WebSocket (subscriptions)
- **Base URL**: `http://127.0.0.1:<port>/rpc` (HTTP)
- **WebSocket URL**: `ws://127.0.0.1:<port>/ws` (WebSocket)
- **Content-Type**: `application/json`
- **Default Port**: `8545` (auto-incremented on conflict)

## Request Format

```json
{
  "jsonrpc": "2.0",
  "method": "method.name",
  "params": [...] or {...},
  "id": 1
}
```

## Response Format

### Success Response
```json
{
  "jsonrpc": "2.0",
  "result": ...,
  "id": 1
}
```

### Error Response
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32600,
    "message": "Invalid Request",
    "data": "..."
  },
  "id": 1
}
```

## Standard JSON-RPC Error Codes

| Code | Message | Meaning |
|------|---------|---------|
| -32700 | Parse error | Invalid JSON |
| -32600 | Invalid Request | Malformed RPC request |
| -32601 | Method not found | Method doesn't exist |
| -32602 | Invalid params | Invalid method parameters |
| -32603 | Internal error | Server-side error |
| -32000 | Server error | Application-level error |

## RPC Methods Used by Wallet

### Health & System

#### `node.ping`

Health check endpoint.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "node.ping",
  "params": [],
  "id": 1
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": "pong",
  "id": 1
}
```

**Usage**: NodeManager uses this to verify node readiness after startup.

---

### Chain Information

#### `chain.getChainId`

Get the network chain ID.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "chain.getChainId",
  "params": [],
  "id": 2
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": 1337,
  "id": 2
}
```

**Values**:
- `1` - Mainnet
- `2` - Testnet
- `1337` - Devnet

---

#### `chain.getHead`

Get the current chain head (latest block).

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "chain.getHead",
  "params": [],
  "id": 3
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": {
    "number": 12345,
    "hash": "0x1234567890abcdef...",
    "parentHash": "0xfedcba0987654321...",
    "timestamp": 1704123456,
    "miner": "anim1abcd...",
    "gasUsed": "21000",
    "gasLimit": "30000000",
    "stateRoot": "0x...",
    "receiptsRoot": "0x...",
    "transactionsRoot": "0x..."
  },
  "id": 3
}
```

**Usage**: Display current block height and sync status.

---

#### `chain.getBlockByNumber`

Get block details by block number.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "chain.getBlockByNumber",
  "params": [12345, true],
  "id": 4
}
```

**Parameters**:
- `number` (integer or "latest"): Block number
- `fullTx` (boolean): If true, include full transaction objects

**Response**: Similar to `chain.getHead` result.

---

#### `chain.getBlockByHash`

Get block details by block hash.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "chain.getBlockByHash",
  "params": ["0x1234...abcd", false],
  "id": 5
}
```

**Parameters**:
- `hash` (string): Block hash (hex with 0x prefix)
- `fullTx` (boolean): If true, include full transaction objects

---

### Sync Status

#### `sync.getStatus`

Get synchronization status.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "sync.getStatus",
  "params": [],
  "id": 6
}
```

**Response** (syncing):
```json
{
  "jsonrpc": "2.0",
  "result": {
    "syncing": true,
    "currentBlock": 1000,
    "highestBlock": 12345,
    "startingBlock": 0,
    "progress": 8.1
  },
  "id": 6
}
```

**Response** (synced):
```json
{
  "jsonrpc": "2.0",
  "result": {
    "syncing": false,
    "currentBlock": 12345,
    "highestBlock": 12345
  },
  "id": 6
}
```

**Usage**: NodeManager displays sync progress in UI.

---

### P2P Network

#### `p2p.listPeers`

List connected peers.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "p2p.listPeers",
  "params": [],
  "id": 7
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": [
    {
      "peerId": "12D3KooW...",
      "address": "/ip4/192.168.1.100/tcp/30333",
      "direction": "outbound",
      "protocols": ["animica/1.0.0", "gossip/1.0.0"],
      "score": 95.5
    },
    ...
  ],
  "id": 7
}
```

**Usage**: Display peer count in status bar.

---

### State Queries

#### `state.getBalance`

Get account balance.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "state.getBalance",
  "params": ["anim1abc123...", "latest"],
  "id": 8
}
```

**Parameters**:
- `address` (string): Account address (Bech32 format)
- `block` (string, optional): "latest", "pending", or block number

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": "1000000000000000000",
  "id": 8
}
```

**Note**: Balance is in wei (1 ANM = 10^18 wei).

---

#### `state.getNonce`

Get account nonce (transaction count).

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "state.getNonce",
  "params": ["anim1abc123...", "latest"],
  "id": 9
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": 42,
  "id": 9
}
```

**Usage**: Build transactions with correct nonce.

---

### Transactions

#### `tx.sendRawTransaction`

Submit a signed transaction to the mempool.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "tx.sendRawTransaction",
  "params": ["0x1234567890abcdef..."],
  "id": 10
}
```

**Parameters**:
- `data` (string): Signed transaction bytes (hex with 0x prefix)

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": "0xabcdef1234567890...",
  "id": 10
}
```

**Result**: Transaction hash.

**Errors**:
- `-32000`: Invalid transaction (e.g., bad signature, insufficient gas)
- `-32001`: Nonce too low
- `-32002`: Insufficient funds

---

#### `tx.getTransactionByHash`

Get transaction details by hash.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "tx.getTransactionByHash",
  "params": ["0xabcdef..."],
  "id": 11
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": {
    "hash": "0xabcdef...",
    "from": "anim1abc...",
    "to": "anim1def...",
    "value": "1000000000000000000",
    "nonce": 42,
    "gas": "21000",
    "gasPrice": "1000000000",
    "data": "0x",
    "chainId": 1337,
    "blockNumber": 12345,
    "blockHash": "0x1234...",
    "transactionIndex": 0
  },
  "id": 11
}
```

**Response** (pending tx):
```json
{
  "jsonrpc": "2.0",
  "result": {
    "hash": "0xabcdef...",
    "from": "anim1abc...",
    "to": "anim1def...",
    "value": "1000000000000000000",
    "nonce": 42,
    "gas": "21000",
    "gasPrice": "1000000000",
    "data": "0x",
    "chainId": 1337,
    "blockNumber": null,
    "blockHash": null,
    "transactionIndex": null
  },
  "id": 11
}
```

---

#### `tx.getTransactionReceipt`

Get transaction receipt (after mining).

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "tx.getTransactionReceipt",
  "params": ["0xabcdef..."],
  "id": 12
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": {
    "transactionHash": "0xabcdef...",
    "blockNumber": 12345,
    "blockHash": "0x1234...",
    "transactionIndex": 0,
    "from": "anim1abc...",
    "to": "anim1def...",
    "gasUsed": "21000",
    "cumulativeGasUsed": "21000",
    "status": "success",
    "logs": []
  },
  "id": 12
}
```

**Response** (not mined yet):
```json
{
  "jsonrpc": "2.0",
  "result": null,
  "id": 12
}
```

**Usage**: Check if transaction was successful after sending.

---

### Mempool

#### `mempool.list`

List pending transactions in mempool.

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "mempool.list",
  "params": [],
  "id": 13
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": [
    {
      "hash": "0xabc...",
      "from": "anim1abc...",
      "to": "anim1def...",
      "value": "1000000000000000000",
      "nonce": 42
    },
    ...
  ],
  "id": 13
}
```

**Usage**: Display pending transaction count.

---

## WebSocket Subscriptions

**Note**: WebSocket support is available but not required for initial wallet implementation. This section documents future functionality.

### Connection

```javascript
const ws = new WebSocket('ws://127.0.0.1:8545/ws');
```

### Subscribe to New Blocks

**Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "subscribe",
  "params": ["newHeads"],
  "id": 1
}
```

**Response**:
```json
{
  "jsonrpc": "2.0",
  "result": "0x1234567890abcdef",
  "id": 1
}
```

**Notifications**:
```json
{
  "jsonrpc": "2.0",
  "method": "subscription",
  "params": {
    "subscription": "0x1234567890abcdef",
    "result": {
      "number": 12346,
      "hash": "0x...",
      ...
    }
  }
}
```

**Unsubscribe**:
```json
{
  "jsonrpc": "2.0",
  "method": "unsubscribe",
  "params": ["0x1234567890abcdef"],
  "id": 2
}
```

---

## Error Handling

### Common Errors

#### Method Not Found
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32601,
    "message": "Method not found",
    "data": "method.name"
  },
  "id": 1
}
```

**Cause**: Node version doesn't support the method.  
**Handling**: Fall back to alternative method or display error.

#### Invalid Params
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Invalid params",
    "data": "Expected 2 arguments, got 1"
  },
  "id": 1
}
```

**Cause**: Wrong number or type of parameters.  
**Handling**: Fix request format or display error.

#### Internal Error
```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32603,
    "message": "Internal error",
    "data": "Database error: ..."
  },
  "id": 1
}
```

**Cause**: Node encountered an error processing request.  
**Handling**: Retry with backoff or display error.

### Timeout Handling

- **Default timeout**: 30 seconds per request
- **Health check timeout**: 5 seconds
- **Long-running operations**: May need longer timeouts

### Retry Strategy

For transient errors:
1. Wait 1 second
2. Retry up to 3 times
3. Exponential backoff: 1s, 2s, 4s

For permanent errors (e.g., invalid params):
- Do not retry
- Display error to user

---

## Implementation Notes

### AnimicaRpcClient Class

The `AnimicaRpcClient` C++ class will wrap these methods with a type-safe interface:

```cpp
class AnimicaRpcClient : public QObject {
    Q_OBJECT
public:
    // Connection
    void setEndpoint(const QString& url);
    bool isConnected();
    
    // Health
    QNetworkReply* ping();
    
    // Chain info
    QNetworkReply* getChainId();
    QNetworkReply* getHead();
    QNetworkReply* getBlockByNumber(int number, bool fullTx = false);
    
    // Sync status
    QNetworkReply* getSyncStatus();
    
    // State
    QNetworkReply* getBalance(const QString& address, const QString& block = "latest");
    QNetworkReply* getNonce(const QString& address, const QString& block = "latest");
    
    // Transactions
    QNetworkReply* sendRawTransaction(const QByteArray& signedTx);
    QNetworkReply* getTransaction(const QString& hash);
    QNetworkReply* getReceipt(const QString& hash);
    
    // P2P
    QNetworkReply* listPeers();

signals:
    void connected();
    void disconnected();
    void error(const QString& message);
};
```

### Request ID Management

- Use sequential integers: 1, 2, 3, ...
- Track pending requests in QMap<int, QNetworkReply*>
- Handle out-of-order responses

### JSON Serialization

Use Qt's JSON classes:
- `QJsonDocument` for parsing/serializing
- `QJsonObject` for request/response objects
- `QJsonArray` for arrays

### Network Configuration

Use Qt's `QNetworkAccessManager`:
- Single instance per application
- Connection pooling (reuse HTTP connections)
- Automatic retries for network errors

---

## Testing

### Manual Testing

Test RPC methods with curl:

```bash
# Health check
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"node.ping","params":[],"id":1}'

# Get chain ID
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"chain.getChainId","params":[],"id":1}'

# Get balance
curl -X POST http://127.0.0.1:8545/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"state.getBalance","params":["anim1abc...", "latest"],"id":1}'
```

### Unit Testing

Create mock RPC server for testing:
- Return canned responses
- Test error handling
- Test timeout scenarios

---

## Future Extensions

### Additional Methods

As wallet features expand, we may need:

- `state.getCode` - Get contract bytecode
- `state.call` - Call contract method (read-only)
- `eth_estimateGas` - Estimate transaction gas
- `miner.getWork` - Get mining work (if user mines)
- `miner.submitShare` - Submit mining solution

### WebSocket Usage

For real-time updates:
- Subscribe to new blocks
- Subscribe to pending transactions
- Subscribe to logs (contract events)

---

## References

- Animica RPC Implementation: `rpc/server.py`
- JSON-RPC Methods: `rpc/methods/` directory
- RPC Configuration: `rpc/config.py`
- Network Access Manager: https://doc.qt.io/qt-6/qnetworkaccessmanager.html
- JSON in Qt: https://doc.qt.io/qt-6/json.html
