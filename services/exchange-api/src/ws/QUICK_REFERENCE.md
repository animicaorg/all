# WebSocket Server - Quick Reference

## File Structure
```
src/ws/
├── protocol.ts          # Message types & constants (31 exports)
├── auth.ts              # Authentication (3 exports)
├── subscriptions.ts     # Subscription management (7 exports)
├── snapshot.ts          # Snapshot delivery (4 exports)
├── diff.ts              # Diff streaming (11 exports)
├── heartbeat.ts         # Heartbeat mechanism (6 exports)
├── backpressure.ts      # Queue management (6 exports)
├── multiplex.ts         # Channel routing (9 exports)
├── server.ts            # Main server (3 exports)
├── index.ts             # Public API (all exports)
├── example.ts           # Usage examples
├── server.test.ts       # Integration tests
├── README.md            # Full documentation
└── IMPLEMENTATION_SUMMARY.md
```

## Quick Start

```typescript
import { createWebSocketServer } from './ws/index.js';

const server = createWebSocketServer({
  prisma,
  redis,
  config,
  logger,
  marketDataCache,
});

// Broadcast
server.getMultiplexer().broadcast('book:BTC_USD', message);

// Stats
const stats = server.getStats();

// Shutdown
await server.stop();
```

## Client Connection

```javascript
const ws = new WebSocket('ws://localhost:3001');

// Auth
ws.send(JSON.stringify({
  op: 'auth',
  apiKey: 'key_xxx',
  timestamp: Date.now(),
  nonce: crypto.randomUUID(),
  signature: computeHMAC(...)
}));

// Subscribe
ws.send(JSON.stringify({
  op: 'subscribe',
  id: 1,
  channels: [
    { name: 'book', market: 'BTC_USD' },
    { name: 'trades', market: 'BTC_USD' }
  ]
}));

// Listen
ws.on('message', (data) => {
  const msg = JSON.parse(data);
  // Handle snapshot, update, trade, ticker, etc.
});
```

## Message Types

### Client → Server
- `auth` - Authenticate with API key
- `subscribe` - Subscribe to channels
- `unsubscribe` - Unsubscribe from channels
- `ping` - Heartbeat ping

### Server → Client
- `pong` - Heartbeat response
- `subscribed` - Subscription confirmation
- `unsubscribed` - Unsubscription confirmation
- `snapshot` - Initial orderbook state
- `update` - Orderbook diff
- `trade` - Trade execution
- `ticker` - Ticker update
- `candle` - OHLCV candle
- `order` - Order update (private)
- `balance` - Balance update (private)
- `error` - Error message

## Channels

### Public
- `book:MARKET` - Orderbook
- `trades:MARKET` - Trades
- `tickers:MARKET` - Tickers
- `candles:MARKET:INTERVAL` - Candles

### Private (auth required)
- `orders` - User orders
- `balances` - User balances

## Priority Levels
1. **CRITICAL** (0) - Snapshots, errors (never dropped)
2. **HIGH** (1) - Book updates, orders, balances
3. **NORMAL** (2) - Trades
4. **LOW** (3) - Tickers, candles (dropped first)

## Configuration
```env
WS_PORT=3001
WS_HEARTBEAT_INTERVAL_MS=15000      # Ping every 15s
WS_HEARTBEAT_TIMEOUT_MS=45000        # Timeout after 45s
WS_MAX_SUBSCRIPTIONS_PER_CLIENT=50   # Max channels per connection
WS_MAX_OUTGOING_QUEUE_SIZE=1000      # Max queued messages
```

## Broadcasting Examples

```typescript
const multiplexer = server.getMultiplexer();

// Orderbook update
multiplexer.broadcast('book:BTC_USD', {
  type: 'update',
  channel: 'book',
  market: 'BTC_USD',
  seq: 12345,
  changes: { bids: [['50000', '1.5']] },
  ts: Date.now()
});

// Trade
multiplexer.broadcast('trades:BTC_USD', {
  type: 'trade',
  market: 'BTC_USD',
  trade_id: 'trade_123',
  price: '50000',
  size: '0.5',
  side: 'buy',
  ts: Date.now()
});

// Ticker
multiplexer.broadcast('tickers:BTC_USD', {
  type: 'ticker',
  market: 'BTC_USD',
  last: '50000',
  bid: '49999',
  ask: '50001',
  volume: '1234',
  ts: Date.now()
});
```

## Error Codes
- `AUTH_REQUIRED` - Private channel needs authentication
- `AUTH_FAILED` - Invalid API key or signature
- `INVALID_MESSAGE` - Malformed JSON
- `INVALID_CHANNEL` - Unknown channel or missing params
- `TOO_MANY_SUBSCRIPTIONS` - Exceeded limit
- `SEQUENCE_GAP` - Orderbook gap, resubscribe
- `INTERNAL_ERROR` - Server error

## Close Codes
- `1000` - Normal closure
- `1001` - Going away (shutdown)
- `1002` - Protocol error
- `1008` - Policy violation
- `1011` - Internal error
- `1013` - Service overload

## Key Classes

```typescript
// Server
ExchangeWebSocketServer - Main server
  .start() - Start listening
  .stop() - Graceful shutdown
  .getMultiplexer() - Get multiplexer
  .getStats() - Get statistics

// Subscriptions
ConnectionSubscriptions - Per-connection subs
  .add(key) - Add subscription
  .remove(key) - Remove subscription
  .has(key) - Check subscription
  .getAll() - Get all subs

SubscriptionManager - Global channel→connections
  .addSubscriber(channel, connId)
  .removeSubscriber(channel, connId)
  .getSubscribers(channel)

// Multiplexing
ChannelMultiplexer - Message routing
  .broadcast(channel, msg) - Broadcast to channel
  .sendToConnection(connId, msg) - Direct send
  .getSubscriberCount(channel)

// Queues
MessageQueue - Per-connection queue
  .enqueue(msg) - Add message (priority-based)
  .dequeue() - Get next message
  .size() - Queue size
  .isFull() - Check if full

QueueManager - Manage all queues
  .createQueue(connId)
  .getQueue(connId)
  .removeQueue(connId)

// Heartbeat
HeartbeatManager - Connection liveness
  .register(connId)
  .markPongReceived(connId)
  .isAlive(connId)
  .getDeadConnections()
```

## Helper Functions

```typescript
// Protocol
buildChannelKey(config) - Build channel key
parseChannelKey(key) - Parse channel key
getMessagePriority(msg) - Get priority level
isAuthMessage(msg) - Type guard
isSubscribeMessage(msg) - Type guard

// Subscriptions
validateChannel(channel, isAuth) - Validate channel
handleSubscribe(msg, ...) - Handle subscribe
handleUnsubscribe(msg, ...) - Handle unsubscribe

// Snapshots
sendSnapshot(channel, ...) - Send snapshot
sendSnapshots(channels, ...) - Send multiple

// Diff
streamOrderbookDiff(market, diff, ...) - Stream diff
streamTrade(trade, ...) - Stream trade
streamTicker(ticker, ...) - Stream ticker

// Backpressure
shouldDisconnectForBackpressure(queue) - Check disconnect
getBackpressureStatus(queue) - Get status string
```

## Testing

```bash
# Run tests
npm test ws/server.test.ts

# Connect with wscat
npm install -g wscat
wscat -c ws://localhost:3001
```

## Performance Targets
- Latency: < 10ms
- Throughput: 10,000+ msg/sec per conn
- Connections: 1,000+ concurrent
- Memory: ~1MB per connection

## Architecture Pattern

```
Connection → Queue → Multiplexer → Channel → Subscribers
     ↓         ↓          ↓            ↓          ↓
  State   Priority   Routing      Filtering  Delivery
```

## State Management

```typescript
ConnectionState {
  id: string
  ws: WebSocket
  userId?: string
  apiKeyId?: string
  isAuthenticated: boolean
  subscriptions: ConnectionSubscriptions
}
```

## Integration

```typescript
// In main HTTP server
import { createWebSocketServer } from './ws/index.js';

const wsServer = createWebSocketServer({ ... });

// In matching engine callback
matchingEngine.on('trade', (trade) => {
  const multiplexer = wsServer.getMultiplexer();
  multiplexer.broadcast(`trades:${trade.market}`, tradeMessage);
});

// In market data updater
marketDataCache.on('update', (market, diff, seq) => {
  streamOrderbookDiff(market, diff, seq, ...);
});
```

## Security Checklist
- ✅ HMAC signature verification
- ✅ Nonce replay protection
- ✅ Timestamp window validation
- ✅ Private channel authentication
- ✅ Max subscriptions limit
- ✅ Rate limiting via backpressure
- ✅ Automatic cleanup of dead connections

## Deployment Checklist
- ✅ Configure environment variables
- ✅ Setup Redis for nonce storage
- ✅ Configure reverse proxy (nginx/haproxy)
- ✅ Setup monitoring/metrics
- ✅ Configure logging level
- ✅ Test graceful shutdown
- ✅ Load test with realistic traffic

## Troubleshooting

**Connection drops**: Check heartbeat timeout
**Slow updates**: Check queue backpressure
**Auth fails**: Verify signature computation
**No messages**: Check subscription status
**High memory**: Check queue sizes and connection count
