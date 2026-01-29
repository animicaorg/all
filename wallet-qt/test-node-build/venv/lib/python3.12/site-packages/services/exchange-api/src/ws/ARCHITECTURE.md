# WebSocket Server Architecture Diagrams

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Exchange WebSocket Server                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────────┐      ┌──────────────┐     ┌──────────────┐   │
│  │   Protocol   │─────▶│     Auth     │────▶│Subscriptions │   │
│  │   Parsing    │      │  Validation  │     │  Management  │   │
│  └──────────────┘      └──────────────┘     └──────────────┘   │
│         │                                             │          │
│         │                                             │          │
│         ▼                                             ▼          │
│  ┌──────────────┐      ┌──────────────┐     ┌──────────────┐   │
│  │   Message    │◀────▶│   Queue      │◀───▶│  Multiplex   │   │
│  │   Handler    │      │  Management  │     │   Routing    │   │
│  └──────────────┘      └──────────────┘     └──────────────┘   │
│         │                      │                     │          │
│         │                      │                     │          │
│         ▼                      ▼                     ▼          │
│  ┌──────────────┐      ┌──────────────┐     ┌──────────────┐   │
│  │  Snapshot    │      │ Backpressure │     │  Heartbeat   │   │
│  │  Delivery    │      │   Handler    │     │   Monitor    │   │
│  └──────────────┘      └──────────────┘     └──────────────┘   │
│         │                      │                     │          │
│         └──────────────────────┴─────────────────────┘          │
│                                │                                 │
│                                ▼                                 │
│                       ┌──────────────┐                          │
│                       │   Diff       │                          │
│                       │  Streaming   │                          │
│                       └──────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
         │                                          │
         ▼                                          ▼
┌──────────────────┐                    ┌──────────────────┐
│  MarketDataCache │                    │  Prisma + Redis  │
└──────────────────┘                    └──────────────────┘
```

## Connection Lifecycle

```
┌─────────┐
│ Client  │
└────┬────┘
     │
     │ 1. WebSocket Connect
     ▼
┌─────────────────────┐
│ Connection Created  │──────┐
│ - Generate ID       │      │
│ - Create State      │      │
│ - Register          │      │
└─────────────────────┘      │
     │                       │
     │ 2. Auth Message       │
     ▼                       │
┌─────────────────────┐      │
│ Authenticate        │      │
│ - Verify Signature  │      │
│ - Check Nonce       │      │
│ - Set User Info     │      │
└─────────────────────┘      │
     │                       │
     │ 3. Subscribe          │
     ▼                       │
┌─────────────────────┐      │
│ Subscribe Channels  │      │
│ - Validate          │      │
│ - Track Subs        │      │
│ - Send Snapshots    │      │
└─────────────────────┘      │
     │                       │
     │ 4. Receive Updates    │
     ▼                       │
┌─────────────────────┐      │
│ Stream Messages     │      │
│ - Queue Messages    │      │
│ - Apply Priority    │      │
│ - Handle Backpressure│     │
└─────────────────────┘      │
     │                       │
     │ 5. Heartbeat          │
     ▼                       │
┌─────────────────────┐      │
│ Ping/Pong           │◀─────┘
│ - Send Ping         │  Monitor
│ - Wait Pong         │  Liveness
│ - Timeout Check     │
└─────────────────────┘
     │
     │ 6. Disconnect
     ▼
┌─────────────────────┐
│ Cleanup             │
│ - Remove Subs       │
│ - Clear Queue       │
│ - Unregister        │
└─────────────────────┘
```

## Message Flow

```
┌─────────┐
│ Client  │
└────┬────┘
     │
     │ Send Message
     ▼
┌─────────────────────┐
│   Parse JSON        │
└─────────────────────┘
     │
     ├─── auth ────────▶ authenticateWebSocket()
     │                        │
     │                        ▼
     │                   Verify Signature
     │                        │
     │                        ▼
     │                   Check Nonce
     │                        │
     │                        ▼
     │                   Set User State
     │
     ├─── subscribe ───▶ handleSubscribe()
     │                        │
     │                        ▼
     │                   Validate Channels
     │                        │
     │                        ▼
     │                   Add Subscriptions
     │                        │
     │                        ▼
     │                   Send Snapshots
     │
     ├─── unsubscribe ─▶ handleUnsubscribe()
     │                        │
     │                        ▼
     │                   Remove Subscriptions
     │
     └─── ping ────────▶ createPongMessage()
                              │
                              ▼
                         Send Pong
```

## Broadcasting Flow

```
┌───────────────────┐
│ Market Data Event │
│ (Trade, Update)   │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Validate Message  │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Get Subscribers   │────────┐
│ for Channel       │        │
└─────────┬─────────┘        │
          │                  │
          ▼                  │ SubscriptionManager
┌───────────────────┐        │ channelKey → Set<connId>
│ For Each          │◀───────┘
│ Subscriber        │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Get Connection    │────────┐
│ State             │        │
└─────────┬─────────┘        │
          │                  │
          ▼                  │ Multiplexer
┌───────────────────┐        │ connId → Connection
│ Enqueue Message   │◀───────┘
│ (with priority)   │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Check Queue       │
│ - Is Full?        │
│ - Drop Low Pri?   │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Send to WebSocket │
└─────────┬─────────┘
          │
          ▼
     ┌─────────┐
     │ Client  │
     └─────────┘
```

## Queue Priority System

```
Incoming Message
      │
      ▼
┌──────────────────┐
│ Get Priority     │
│ - CRITICAL: 0    │
│ - HIGH: 1        │
│ - NORMAL: 2      │
│ - LOW: 3         │
└────────┬─────────┘
         │
         ▼
    Queue Full?
         │
    ┌────┴────┐
    │         │
   YES       NO
    │         │
    │         └────▶ Add to Queue
    │
    ▼
Find Lowest Priority
in Queue
    │
    ├─── Lower than new? ───▶ Drop old, add new
    │
    └─── Higher/Equal? ─────▶ Drop new

Queue Processing:
┌──────────────────┐
│ Sort by Priority │
│ (0 → 3)          │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Within Priority  │
│ Sort by Time     │
│ (FIFO)           │
└────────┬─────────┘
         │
         ▼
    Send Message
```

## Heartbeat Monitor

```
┌─────────────────────────┐
│ HeartbeatManager        │
│                         │
│ Every 5s:               │
│                         │
│  1. Get connections     │
│     needing ping        │
│     (lastPing + 15s)    │
│                         │
│  2. Send ping to each   │
│                         │
│  3. Check for dead      │
│     connections         │
│     (noPong + 45s)      │
│                         │
│  4. Terminate dead      │
│                         │
└─────────────────────────┘

Timeline:
─────────────────────────────────────────▶ Time
│         │         │         │         │
0s       15s       30s       45s       60s
│         │         │         │         │
Start   Ping1     Ping2     Ping3   Timeout
         │         │         │         │
        Pong?     Pong?     Pong?    Dead!
```

## Subscription State

```
Global State:
┌────────────────────────────────────┐
│ SubscriptionManager                │
│                                    │
│ channelKey → Set<connectionId>     │
│                                    │
│ "book:BTC_USD" → {conn1, conn2}   │
│ "trades:ETH_USD" → {conn1, conn3} │
│ "tickers:BTC_USD" → {conn2}       │
└────────────────────────────────────┘

Per-Connection State:
┌────────────────────────────────────┐
│ ConnectionSubscriptions (conn1)    │
│                                    │
│ subscriptions: Set<channelKey>     │
│                                    │
│ {"book:BTC_USD", "trades:ETH_USD"}│
└────────────────────────────────────┘

When Broadcasting to "book:BTC_USD":
1. Look up subscribers: {conn1, conn2}
2. For each connection:
   - Get connection state
   - Enqueue message in queue
   - Send via WebSocket
```

## Security Flow

```
┌─────────┐
│ Client  │
└────┬────┘
     │
     │ Auth Message
     │ {apiKey, timestamp, nonce, signature}
     ▼
┌─────────────────────┐
│ Validate Timestamp  │───── Outside window? ──▶ Reject
│ (±30s)              │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Look up API Key     │───── Not found? ────────▶ Reject
│ (Prisma)            │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Check Revoked       │───── Is revoked? ───────▶ Reject
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Compute Signature   │
│ HMAC(secret,        │
│   timestamp + nonce │
│   + "WS" + "/" )    │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Verify Signature    │───── No match? ─────────▶ Reject
│ (timing-safe)       │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Check Nonce         │───── Already used? ─────▶ Reject
│ (Redis/DB)          │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│ Store Nonce         │
│ (TTL: 5 min)        │
└─────────┬───────────┘
          │
          ▼
     Authenticated!
```

## Data Structures

```typescript
// Connection State
connections: Map<string, ConnectionState>
  connectionId → {
    id: string
    ws: WebSocket
    userId?: string
    apiKeyId?: string
    scopes?: string[]
    subscriptions: ConnectionSubscriptions
    isAuthenticated: boolean
    createdAt: number
  }

// Subscription Management
SubscriptionManager {
  channelSubscribers: Map<string, Set<string>>
    channelKey → Set<connectionId>
}

ConnectionSubscriptions {
  subscriptions: Set<string>
    Set<channelKey>
}

// Queue Management
QueueManager {
  queues: Map<string, MessageQueue>
    connectionId → MessageQueue
}

MessageQueue {
  queue: QueuedMessage[]
  maxSize: number
  droppedCount: number
  sentCount: number
}

// Heartbeat Management
HeartbeatManager {
  states: Map<string, HeartbeatState>
    connectionId → {
      lastPing: number
      lastPong: number
      isAlive: boolean
      missedPongs: number
    }
}

// Channel Multiplexing
ChannelMultiplexer {
  connections: Map<string, MultiplexConnection>
    connectionId → {
      connectionId: string
      subscriptions: ConnectionSubscriptions
      queue: MessageQueue
      sendMessage: (msg) => void
    }
}
```

## Performance Optimization

```
Optimization Points:
┌────────────────────────────────────┐
│ 1. Map-based lookups O(1)          │
│    - connections                   │
│    - subscriptions                 │
│    - queues                        │
│                                    │
│ 2. Priority queue for messages     │
│    - Sort only on dequeue          │
│    - O(n log n) but rare           │
│                                    │
│ 3. Throttling for tickers          │
│    - Max 1/sec per market          │
│    - Reduces bandwidth             │
│                                    │
│ 4. Lazy snapshot creation          │
│    - Only on subscribe             │
│    - Cached in MarketDataCache     │
│                                    │
│ 5. Connection pooling ready        │
│    - Stateless design              │
│    - Easy horizontal scaling       │
└────────────────────────────────────┘
```
