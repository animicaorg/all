/**
 * WebSocket Server Module
 * 
 * Production-grade WebSocket server for exchange-api with:
 * - Multiplexed channel subscriptions
 * - Snapshot/diff streaming with sequence numbers
 * - Backpressure and queue management
 * - Heartbeat mechanism
 * - API key authentication
 * - Graceful shutdown
 */

// Main server
export {
  ExchangeWebSocketServer,
  createWebSocketServer,
  type WebSocketServerOptions,
} from './server.js';

// Protocol definitions
export {
  type ClientMessage,
  type ServerMessage,
  type DataMessage,
  type AuthMessage,
  type SubscribeMessage,
  type UnsubscribeMessage,
  type PingMessage,
  type PongMessage,
  type SubscribedMessage,
  type UnsubscribedMessage,
  type OrderbookSnapshotMessage,
  type OrderbookUpdateMessage,
  type TradeMessage,
  type TickerMessage,
  type CandleMessage,
  type OrderMessage,
  type BalanceMessage,
  type ErrorMessage,
  type ChannelConfig,
  MessagePriority,
  CHANNEL_TYPES,
  PRIVATE_CHANNELS,
  WS_CLOSE_CODES,
  ERROR_CODES,
  buildChannelKey,
  parseChannelKey,
  getMessagePriority,
} from './protocol.js';

// Authentication
export {
  authenticateWebSocket,
  hasScope,
  type AuthResult,
} from './auth.js';

// Subscription management
export {
  ConnectionSubscriptions,
  SubscriptionManager,
  validateChannel,
  handleSubscribe,
  handleUnsubscribe,
  cleanupSubscriptions,
  type SubscriptionResult,
  type UnsubscriptionResult,
} from './subscriptions.js';

// Snapshot delivery
export {
  sendSnapshot,
  sendSnapshots,
  sendOrderbookSnapshot,
  sendRecentTrades,
  sendTickerSnapshot,
  type SnapshotOptions,
} from './snapshot.js';

// Diff streaming
export {
  SequenceTracker,
  TickerThrottle,
  streamOrderbookDiff,
  streamTrade,
  streamTicker,
  handleSequenceGap,
  createOrderbookUpdate,
  validateOrderbookDiff,
  validateTrade,
  validateTicker,
} from './diff.js';

// Heartbeat
export {
  HeartbeatManager,
  createPingMessage,
  createPongMessage,
  startHeartbeatChecker,
  stopHeartbeatChecker,
  type HeartbeatState,
} from './heartbeat.js';

// Backpressure
export {
  MessageQueue,
  QueueManager,
  shouldDisconnectForBackpressure,
  getBackpressureStatus,
  type QueuedMessage,
  type QueueStats,
} from './backpressure.js';

// Multiplexing
export {
  ChannelMultiplexer,
  routeMessage,
  routeToMultipleChannels,
  broadcastToBookChannel,
  broadcastToTradesChannel,
  broadcastToTickersChannel,
  sendToOrdersChannel,
  sendToBalancesChannel,
  type MultiplexConnection,
} from './multiplex.js';
