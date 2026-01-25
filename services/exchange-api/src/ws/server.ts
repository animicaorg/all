/**
 * WebSocket Server
 * 
 * Main WebSocket server implementation with full production-grade features:
 * - Multiplexed channel subscriptions
 * - Snapshot/diff streaming
 * - Backpressure handling
 * - Heartbeat mechanism
 * - API key authentication
 * - Graceful shutdown
 */

import { WebSocketServer, WebSocket, RawData } from 'ws';
import type { PrismaClient } from '@prisma/client';
import type { RedisClientType } from 'redis';
import type { Logger } from '../utils/logger.js';
import type { Config } from '../config.js';
import type { MarketDataCache } from '../services/market_data_cache.js';
import { v4 as uuidv4 } from 'uuid';

import {
  type ClientMessage,
  type ServerMessage,
  type ErrorMessage,
  isAuthMessage,
  isSubscribeMessage,
  isUnsubscribeMessage,
  isPingMessage,
  WS_CLOSE_CODES,
  ERROR_CODES,
} from './protocol.js';

import { authenticateWebSocket, type AuthResult } from './auth.js';
import {
  ConnectionSubscriptions,
  SubscriptionManager,
  handleSubscribe,
  handleUnsubscribe,
  cleanupSubscriptions,
} from './subscriptions.js';
import { sendSnapshots, type SnapshotOptions } from './snapshot.js';
import {
  HeartbeatManager,
  createPongMessage,
  startHeartbeatChecker,
  stopHeartbeatChecker,
} from './heartbeat.js';
import {
  QueueManager,
  shouldDisconnectForBackpressure,
  getBackpressureStatus,
} from './backpressure.js';
import {
  ChannelMultiplexer,
  type MultiplexConnection,
} from './multiplex.js';

/**
 * Connection state
 */
interface ConnectionState {
  id: string;
  ws: WebSocket;
  userId?: string;
  apiKeyId?: string;
  scopes?: string[];
  subscriptions: ConnectionSubscriptions;
  isAuthenticated: boolean;
  createdAt: number;
}

/**
 * WebSocket server options
 */
export interface WebSocketServerOptions {
  prisma: PrismaClient;
  redis: RedisClientType | null;
  config: Config;
  logger: Logger;
  marketDataCache: MarketDataCache;
  snapshotOptions?: SnapshotOptions;
}

/**
 * Production-grade WebSocket server
 */
export class ExchangeWebSocketServer {
  private wss?: WebSocketServer;
  private readonly prisma: PrismaClient;
  private readonly redis: RedisClientType | null;
  private readonly config: Config;
  private readonly logger: Logger;
  private readonly marketDataCache: MarketDataCache;
  private readonly snapshotOptions: SnapshotOptions;

  // State management
  private readonly connections: Map<string, ConnectionState> = new Map();
  private readonly subscriptionManager: SubscriptionManager;
  private readonly queueManager: QueueManager;
  private readonly heartbeatManager: HeartbeatManager;
  private readonly multiplexer: ChannelMultiplexer;

  // Lifecycle
  private heartbeatInterval?: NodeJS.Timeout;
  private isShuttingDown: boolean = false;

  constructor(options: WebSocketServerOptions) {
    this.prisma = options.prisma;
    this.redis = options.redis;
    this.config = options.config;
    this.logger = options.logger;
    this.marketDataCache = options.marketDataCache;
    this.snapshotOptions = options.snapshotOptions || {};

    this.subscriptionManager = new SubscriptionManager(this.logger);
    this.queueManager = new QueueManager(this.config, this.logger);
    this.heartbeatManager = new HeartbeatManager(this.config, this.logger);
    this.multiplexer = new ChannelMultiplexer(this.subscriptionManager, this.logger);
  }

  /**
   * Start the WebSocket server
   */
  start(): void {
    const { WS_HOST, WS_PORT } = this.config;

    this.wss = new WebSocketServer({
      host: WS_HOST,
      port: WS_PORT,
    });

    this.wss.on('connection', (ws) => this.handleConnection(ws));
    this.wss.on('error', (error) => this.handleServerError(error));

    // Start heartbeat checker
    this.heartbeatInterval = startHeartbeatChecker(
      this.heartbeatManager,
      (connectionId) => this.sendPing(connectionId),
      (connectionId, reason) => this.terminateConnection(connectionId, reason),
      this.logger
    );

    this.logger.info(
      { host: WS_HOST, port: WS_PORT },
      'WebSocket server started'
    );
  }

  /**
   * Stop the WebSocket server gracefully
   */
  async stop(): Promise<void> {
    if (this.isShuttingDown) {
      return;
    }

    this.isShuttingDown = true;
    this.logger.info('WebSocket server shutting down...');

    // Stop heartbeat checker
    if (this.heartbeatInterval) {
      stopHeartbeatChecker(this.heartbeatInterval);
    }

    // Close all connections
    for (const [connectionId, state] of this.connections.entries()) {
      try {
        state.ws.close(WS_CLOSE_CODES.GOING_AWAY, 'Server shutting down');
      } catch (error) {
        this.logger.warn({ error, connectionId }, 'Error closing connection during shutdown');
      }
    }

    // Close server
    if (this.wss) {
      await new Promise<void>((resolve) => {
        this.wss!.close(() => {
          this.logger.info('WebSocket server stopped');
          resolve();
        });
      });
    }

    // Clear state
    this.connections.clear();
    this.queueManager.clear();
    this.heartbeatManager.clear();
    this.multiplexer.clear();
  }

  /**
   * Handle new WebSocket connection
   */
  private handleConnection(ws: WebSocket): void {
    const connectionId = uuidv4();
    const subscriptions = new ConnectionSubscriptions(
      connectionId,
      this.config,
      this.logger
    );
    const queue = this.queueManager.createQueue(connectionId);

    const state: ConnectionState = {
      id: connectionId,
      ws,
      subscriptions,
      isAuthenticated: false,
      createdAt: Date.now(),
    };

    this.connections.set(connectionId, state);
    this.heartbeatManager.register(connectionId);

    // Register with multiplexer
    const multiplexConnection: MultiplexConnection = {
      connectionId,
      subscriptions,
      queue,
      sendMessage: (msg) => this.sendMessage(connectionId, msg),
    };
    this.multiplexer.registerConnection(multiplexConnection);

    this.logger.info({ connectionId }, 'WebSocket connection established');

    ws.on('message', (data) => this.handleMessage(connectionId, data));
    ws.on('close', (code, reason) => this.handleClose(connectionId, code, reason));
    ws.on('error', (error) => this.handleError(connectionId, error));
    ws.on('pong', () => this.handlePong(connectionId));
  }

  /**
   * Handle incoming message from client
   */
  private async handleMessage(connectionId: string, data: RawData): Promise<void> {
    const state = this.connections.get(connectionId);
    if (!state) {
      return;
    }

    try {
      const message = JSON.parse(data.toString()) as ClientMessage;

      if (isAuthMessage(message)) {
        await this.handleAuthMessage(connectionId, message);
      } else if (isPingMessage(message)) {
        this.handlePingMessage(connectionId, message);
      } else if (isSubscribeMessage(message)) {
        await this.handleSubscribeMessage(connectionId, message);
      } else if (isUnsubscribeMessage(message)) {
        this.handleUnsubscribeMessage(connectionId, message);
      } else {
        this.sendError(connectionId, ERROR_CODES.INVALID_MESSAGE, 'Unknown message type');
      }
    } catch (error) {
      this.logger.warn({ error, connectionId }, 'Failed to parse message');
      this.sendError(connectionId, ERROR_CODES.INVALID_MESSAGE, 'Invalid JSON');
    }
  }

  /**
   * Handle authentication message
   */
  private async handleAuthMessage(
    connectionId: string,
    message: any
  ): Promise<void> {
    const state = this.connections.get(connectionId);
    if (!state) {
      return;
    }

    const result: AuthResult = await authenticateWebSocket(
      message,
      this.prisma,
      this.redis,
      this.config,
      this.logger
    );

    if (result.success && result.userId && result.apiKeyId) {
      state.isAuthenticated = true;
      state.userId = result.userId;
      state.apiKeyId = result.apiKeyId;
      state.scopes = result.scopes;

      this.logger.info(
        { connectionId, userId: result.userId, apiKeyId: result.apiKeyId },
        'WebSocket authenticated'
      );

      // Send success response (implicit - client will know by getting subscribed messages)
    } else {
      this.logger.warn({ connectionId, error: result.error }, 'Authentication failed');
      this.sendError(
        connectionId,
        ERROR_CODES.AUTH_FAILED,
        result.error || 'Authentication failed'
      );
    }
  }

  /**
   * Handle ping message from client
   */
  private handlePingMessage(connectionId: string, message: any): void {
    const pong = createPongMessage(message.ts);
    this.sendMessage(connectionId, pong);
  }

  /**
   * Handle subscribe message
   */
  private async handleSubscribeMessage(
    connectionId: string,
    message: any
  ): Promise<void> {
    const state = this.connections.get(connectionId);
    if (!state) {
      return;
    }

    const result = handleSubscribe(
      message,
      state.subscriptions,
      this.subscriptionManager,
      connectionId,
      state.isAuthenticated,
      this.logger
    );

    if (result.subscribed && result.subscribed.length > 0) {
      // Send confirmation
      this.sendMessage(connectionId, {
        op: 'subscribed',
        id: message.id,
        channels: result.subscribed,
      });

      // Send snapshots for subscribed channels
      await sendSnapshots(
        result.subscribed,
        this.marketDataCache,
        this.prisma,
        (msg) => this.sendMessage(connectionId, msg),
        this.snapshotOptions,
        this.logger
      );
    }

    if (result.failed && result.failed.length > 0) {
      for (const failure of result.failed) {
        this.sendError(
          connectionId,
          ERROR_CODES.INVALID_CHANNEL,
          failure.reason,
          message.id
        );
      }
    }
  }

  /**
   * Handle unsubscribe message
   */
  private handleUnsubscribeMessage(connectionId: string, message: any): void {
    const state = this.connections.get(connectionId);
    if (!state) {
      return;
    }

    const result = handleUnsubscribe(
      message,
      state.subscriptions,
      this.subscriptionManager,
      connectionId,
      this.logger
    );

    if (result.unsubscribed && result.unsubscribed.length > 0) {
      this.sendMessage(connectionId, {
        op: 'unsubscribed',
        id: message.id,
        channels: result.unsubscribed,
      });
    }
  }

  /**
   * Handle connection close
   */
  private handleClose(connectionId: string, code: number, reason: Buffer): void {
    const state = this.connections.get(connectionId);
    if (!state) {
      return;
    }

    this.logger.info(
      { connectionId, code, reason: reason.toString(), userId: state.userId },
      'WebSocket connection closed'
    );

    this.cleanupConnection(connectionId);
  }

  /**
   * Handle connection error
   */
  private handleError(connectionId: string, error: Error): void {
    this.logger.error({ error, connectionId }, 'WebSocket connection error');
    this.cleanupConnection(connectionId);
  }

  /**
   * Handle pong from client
   */
  private handlePong(connectionId: string): void {
    this.heartbeatManager.markPongReceived(connectionId);
  }

  /**
   * Handle server-level error
   */
  private handleServerError(error: Error): void {
    this.logger.error({ error }, 'WebSocket server error');
  }

  /**
   * Send a ping to a connection
   */
  private sendPing(connectionId: string): void {
    const state = this.connections.get(connectionId);
    if (!state || state.ws.readyState !== WebSocket.OPEN) {
      return;
    }

    try {
      state.ws.ping();
    } catch (error) {
      this.logger.warn({ error, connectionId }, 'Failed to send ping');
    }
  }

  /**
   * Send a message to a connection
   */
  private sendMessage(connectionId: string, message: ServerMessage): void {
    const state = this.connections.get(connectionId);
    if (!state || state.ws.readyState !== WebSocket.OPEN) {
      return;
    }

    // Check backpressure
    const queue = this.queueManager.getQueue(connectionId);
    if (queue && shouldDisconnectForBackpressure(queue)) {
      this.logger.warn(
        { connectionId, queueStats: queue.getStats() },
        'Disconnecting due to persistent backpressure'
      );
      this.terminateConnection(connectionId, 'Persistent backpressure');
      return;
    }

    try {
      const data = JSON.stringify(message);
      state.ws.send(data);

      // Dequeue message if it was queued
      if (queue && !queue.isEmpty()) {
        queue.dequeue();
      }
    } catch (error) {
      this.logger.error({ error, connectionId }, 'Failed to send message');
    }
  }

  /**
   * Send an error message to a connection
   */
  private sendError(
    connectionId: string,
    code: string,
    message: string,
    id?: number
  ): void {
    const errorMessage: ErrorMessage = {
      type: 'error',
      code,
      message,
      id,
    };
    this.sendMessage(connectionId, errorMessage);
  }

  /**
   * Terminate a connection
   */
  private terminateConnection(connectionId: string, reason: string): void {
    const state = this.connections.get(connectionId);
    if (!state) {
      return;
    }

    this.logger.info({ connectionId, reason, userId: state.userId }, 'Terminating connection');

    try {
      state.ws.close(WS_CLOSE_CODES.GOING_AWAY, reason);
    } catch (error) {
      this.logger.warn({ error, connectionId }, 'Error terminating connection');
    }

    this.cleanupConnection(connectionId);
  }

  /**
   * Cleanup connection state
   */
  private cleanupConnection(connectionId: string): void {
    const state = this.connections.get(connectionId);
    if (!state) {
      return;
    }

    // Cleanup subscriptions
    cleanupSubscriptions(
      state.subscriptions,
      this.subscriptionManager,
      connectionId,
      this.logger
    );

    // Cleanup queue
    this.queueManager.removeQueue(connectionId);

    // Cleanup heartbeat
    this.heartbeatManager.unregister(connectionId);

    // Unregister from multiplexer
    this.multiplexer.unregisterConnection(connectionId);

    // Remove connection
    this.connections.delete(connectionId);

    this.logger.debug({ connectionId }, 'Connection cleanup complete');
  }

  /**
   * Get the multiplexer (for external use to broadcast messages)
   */
  getMultiplexer(): ChannelMultiplexer {
    return this.multiplexer;
  }

  /**
   * Get server statistics
   */
  getStats(): {
    connections: number;
    authenticated: number;
    subscriptions: { channels: number; totalSubscriptions: number };
    queues: any;
    heartbeat: any;
  } {
    let authenticated = 0;
    for (const state of this.connections.values()) {
      if (state.isAuthenticated) {
        authenticated++;
      }
    }

    return {
      connections: this.connections.size,
      authenticated,
      subscriptions: this.subscriptionManager.getStats(),
      queues: this.queueManager.getAggregateStats(),
      heartbeat: this.heartbeatManager.getStats(),
    };
  }
}

/**
 * Factory function to create and start WebSocket server
 */
export function createWebSocketServer(
  options: WebSocketServerOptions
): ExchangeWebSocketServer {
  const server = new ExchangeWebSocketServer(options);
  server.start();
  return server;
}
