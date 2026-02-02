# Mempool Watchdog

## Overview

The **Mempool Watchdog** is a continuous monitoring service that ensures transactions from all network nodes are reliably added to local mempools. It complements the existing transaction relay system by actively polling peers for missing transactions.

## Problem Statement

While the P2P transaction relay system handles `TX_INV` messages immediately and fetches transactions on demand, there are scenarios where transactions might be missed:

1. **Lost INV Messages**: Network issues can cause INV announcement messages to be dropped
2. **Race Conditions**: Transactions might be added to a peer's mempool between sync intervals
3. **Peer Inconsistencies**: Different peers may have different transaction sets
4. **Long Sync Intervals**: The default 15-second mempool sync interval is too long for high-throughput scenarios

## Solution: Mempool Watchdog

The watchdog runs continuously (default: every 3 seconds) and actively:

1. **Monitors Known Transactions**: Tracks transactions that peers have announced but haven't been fetched yet
2. **Requests Missing Transactions**: Actively sends `TX_GET` requests for known-but-missing transactions
3. **Complements Existing Relay**: Works alongside the reactive INV/GETDATA protocol
4. **Ensures Completeness**: Provides an additional safety net to catch any missed transactions

## Architecture

```
┌─────────────────────────────────────────────────────┐
│            Transaction Propagation Flow              │
├─────────────────────────────────────────────────────┤
│                                                       │
│  Reactive Path (Immediate):                          │
│  Peer TX_INV → Process → TX_GET → Fetch → Admit     │
│                                                       │
│  Watchdog Path (Continuous):                         │
│  Every 3s: Check known txs → Request missing        │
│                                                       │
│  Periodic Sync (Backup):                             │
│  Every 15s: Request peer mempool snapshot            │
│                                                       │
└─────────────────────────────────────────────────────┘
```

## Configuration

The watchdog can be configured via environment variables:

### `ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_SEC`
- **Description**: Interval in seconds between watchdog checks
- **Default**: `3` seconds
- **Range**: `0.5` - `30` seconds recommended
- **Purpose**: Controls how frequently the watchdog checks for missing transactions

### `ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_LIMIT`
- **Description**: Maximum number of transactions to request per watchdog iteration
- **Default**: `256`
- **Range**: `10` - `1000` recommended
- **Purpose**: Limits the batch size to prevent overwhelming the network

## Usage Examples

### Default Configuration
```bash
# Start node with default watchdog settings (3s interval, 256 tx limit)
python -m rpc.server
```

### Fast Watchdog (High-Throughput)
```bash
# Check every second for missing transactions
export ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_SEC=1
export ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_LIMIT=512
python -m rpc.server
```

### Conservative Watchdog (Low Resources)
```bash
# Check every 10 seconds with smaller batches
export ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_SEC=10
export ANIMICA_P2P_TX_MEMPOOL_WATCHDOG_LIMIT=64
python -m rpc.server
```

## Implementation Details

### TxRelayService.mempool_watchdog_loop()

The watchdog loop performs the following operations:

1. **Sleep**: Wait for `mempool_watchdog_interval_s` seconds
2. **Request Missing**: Call `request_missing_known()` with `mempool_watchdog_limit`
3. **Log Results**: Record how many transactions were requested
4. **Repeat**: Continue until the service is stopped

### Integration with P2PService

The watchdog is started automatically when a P2P node starts:

```python
# In P2PService.start()
self._txrelay_watchdog_task = asyncio.create_task(
    self._txrelay.mempool_watchdog_loop(), 
    name="p2p.txrelay.mempool_watchdog"
)
```

## Monitoring

### Logs

The watchdog produces the following log messages:

```
TX_WATCHDOG_FETCH - Records successful fetch requests
TX_RELAY_HEARTBEAT (loop: watchdog) - Periodic health check
```

### Metrics

View watchdog status in node info:

```bash
# Get node status
curl http://localhost:8545 -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"node.info","id":1}'
```

Look for:
```json
{
  "tx_relay_v2": {
    "mempool_watchdog_interval_s": 3.0,
    "mempool_watchdog_limit": 256
  }
}
```

## Benefits

1. **Improved Reliability**: Catches transactions that might be missed by the reactive system
2. **Lower Latency**: 3-second checks vs 15-second sync intervals
3. **Network Resilience**: Continues to function even when INV messages are lost
4. **Tunable Performance**: Adjust intervals based on network conditions and resources

## Testing

Run the watchdog tests:

```bash
pytest p2p/tests/test_txrelay_watchdog.py -v
```

## Comparison with Existing Systems

| Feature | Before Watchdog | After Watchdog |
|---------|----------------|----------------|
| **Reactive TX_INV** | ✅ Immediate | ✅ Immediate |
| **Periodic Sync** | Every 15s | Every 15s |
| **Active Monitoring** | ❌ None | ✅ Every 3s |
| **Missing TX Recovery** | 15s average | 3s average |
| **Network Resilience** | Moderate | High |

## Related Components

- **TxRelayService** (`p2p/txrelay.py`): Core transaction relay service
- **P2PService** (`p2p/node/p2p_service.py`): P2P node orchestration
- **Mempool** (`mempool/`): Transaction pool management

## Future Enhancements

Potential improvements to the watchdog:

1. **Adaptive Intervals**: Adjust check frequency based on mempool activity
2. **Peer Prioritization**: Request from faster/more reliable peers first
3. **Batch Optimization**: Dynamic batch sizes based on network conditions
4. **Metrics Dashboard**: Real-time visualization of watchdog performance
