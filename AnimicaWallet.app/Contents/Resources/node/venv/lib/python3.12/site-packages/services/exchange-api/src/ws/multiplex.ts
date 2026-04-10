/**
 * Channel Multiplexing
 * 
 * Routes messages to correct handlers and broadcasts to subscribed connections.
 * Maintains channel state and handles subscribe/unsubscribe operations.
 */

import type { Logger } from '../utils/logger.js';
import type { ServerMessage, ChannelConfig } from './protocol.js';
import type { ConnectionSubscriptions, SubscriptionManager } from './subscriptions.js';
import type { MessageQueue } from './backpressure.js';
import { buildChannelKey } from './protocol.js';

/**
 * Connection state for multiplexing
 */
export interface MultiplexConnection {
  connectionId: string;
  subscriptions: ConnectionSubscriptions;
  queue: MessageQueue;
  sendMessage: (msg: ServerMessage) => void;
}

/**
 * Multiplexer for routing messages to subscribed connections
 */
export class ChannelMultiplexer {
  private readonly subscriptionManager: SubscriptionManager;
  private readonly connections: Map<string, MultiplexConnection> = new Map();
  private readonly logger: Logger;

  constructor(subscriptionManager: SubscriptionManager, logger: Logger) {
    this.subscriptionManager = subscriptionManager;
    this.logger = logger;
  }

  /**
   * Register a connection for multiplexing
   */
  registerConnection(connection: MultiplexConnection): void {
    this.connections.set(connection.connectionId, connection);
    this.logger.debug(
      { connectionId: connection.connectionId },
      'Connection registered with multiplexer'
    );
  }

  /**
   * Unregister a connection
   */
  unregisterConnection(connectionId: string): void {
    this.connections.delete(connectionId);
    this.logger.debug(
      { connectionId },
      'Connection unregistered from multiplexer'
    );
  }

  /**
   * Get a connection by ID
   */
  getConnection(connectionId: string): MultiplexConnection | undefined {
    return this.connections.get(connectionId);
  }

  /**
   * Broadcast a message to all subscribers of a channel
   * 
   * @param channelKey - Channel key (e.g., "book:BTC_USD")
   * @param message - Message to broadcast
   * @returns Number of connections that received the message
   */
  broadcast(channelKey: string, message: ServerMessage): number {
    const subscribers = this.subscriptionManager.getSubscribers(channelKey);
    let sentCount = 0;

    for (const connectionId of subscribers) {
      const connection = this.connections.get(connectionId);
      if (!connection) {
        this.logger.warn(
          { connectionId, channelKey },
          'Subscriber connection not found in multiplexer'
        );
        continue;
      }

      try {
        // Enqueue message (handles backpressure)
        const enqueued = connection.queue.enqueue(message);
        if (enqueued) {
          connection.sendMessage(message);
          sentCount++;
        } else {
          this.logger.debug(
            { connectionId, channelKey },
            'Message dropped due to backpressure'
          );
        }
      } catch (error) {
        this.logger.error(
          { error, connectionId, channelKey },
          'Error broadcasting message to connection'
        );
      }
    }

    if (sentCount > 0) {
      this.logger.trace(
        { channelKey, subscribers: subscribers.size, sent: sentCount },
        'Message broadcast to channel subscribers'
      );
    }

    return sentCount;
  }

  /**
   * Send a message to a specific connection
   * 
   * @param connectionId - Connection ID
   * @param message - Message to send
   * @returns true if sent, false if connection not found or backpressure
   */
  sendToConnection(connectionId: string, message: ServerMessage): boolean {
    const connection = this.connections.get(connectionId);
    if (!connection) {
      this.logger.warn({ connectionId }, 'Connection not found for direct send');
      return false;
    }

    try {
      const enqueued = connection.queue.enqueue(message);
      if (enqueued) {
        connection.sendMessage(message);
        return true;
      } else {
        this.logger.debug({ connectionId }, 'Message dropped due to backpressure');
        return false;
      }
    } catch (error) {
      this.logger.error({ error, connectionId }, 'Error sending message to connection');
      return false;
    }
  }

  /**
   * Broadcast to multiple channels at once
   * 
   * @param channelKeys - Array of channel keys
   * @param message - Message to broadcast
   * @returns Total number of connections that received the message
   */
  broadcastToMultiple(channelKeys: string[], message: ServerMessage): number {
    let totalSent = 0;
    const sentConnections = new Set<string>();

    // Collect all unique subscribers across channels
    for (const channelKey of channelKeys) {
      const subscribers = this.subscriptionManager.getSubscribers(channelKey);
      for (const connectionId of subscribers) {
        sentConnections.add(connectionId);
      }
    }

    // Send to each unique connection once
    for (const connectionId of sentConnections) {
      if (this.sendToConnection(connectionId, message)) {
        totalSent++;
      }
    }

    return totalSent;
  }

  /**
   * Get subscriber count for a channel
   */
  getSubscriberCount(channelKey: string): number {
    return this.subscriptionManager.getSubscribers(channelKey).size;
  }

  /**
   * Get total number of connections
   */
  getConnectionCount(): number {
    return this.connections.size;
  }

  /**
   * Get statistics
   */
  getStats(): {
    connections: number;
    channels: number;
    totalSubscriptions: number;
  } {
    const subStats = this.subscriptionManager.getStats();
    return {
      connections: this.connections.size,
      channels: subStats.channels,
      totalSubscriptions: subStats.totalSubscriptions,
    };
  }

  /**
   * Clear all connections
   */
  clear(): void {
    this.connections.clear();
  }
}

/**
 * Route a message to appropriate handler based on channel
 */
export function routeMessage(
  channel: ChannelConfig,
  message: ServerMessage,
  multiplexer: ChannelMultiplexer,
  logger: Logger
): void {
  const channelKey = buildChannelKey(channel);
  
  try {
    multiplexer.broadcast(channelKey, message);
  } catch (error) {
    logger.error(
      { error, channelKey },
      'Error routing message to channel'
    );
  }
}

/**
 * Route messages to multiple channels
 */
export function routeToMultipleChannels(
  channels: ChannelConfig[],
  message: ServerMessage,
  multiplexer: ChannelMultiplexer,
  logger: Logger
): void {
  const channelKeys = channels.map(c => buildChannelKey(c));
  
  try {
    multiplexer.broadcastToMultiple(channelKeys, message);
  } catch (error) {
    logger.error(
      { error, channels: channelKeys },
      'Error routing message to multiple channels'
    );
  }
}

/**
 * Helper to broadcast market data to book channel
 */
export function broadcastToBookChannel(
  market: string,
  message: ServerMessage,
  multiplexer: ChannelMultiplexer
): number {
  const channelKey = `book:${market}`;
  return multiplexer.broadcast(channelKey, message);
}

/**
 * Helper to broadcast to trades channel
 */
export function broadcastToTradesChannel(
  market: string,
  message: ServerMessage,
  multiplexer: ChannelMultiplexer
): number {
  const channelKey = `trades:${market}`;
  return multiplexer.broadcast(channelKey, message);
}

/**
 * Helper to broadcast to tickers channel
 */
export function broadcastToTickersChannel(
  market: string,
  message: ServerMessage,
  multiplexer: ChannelMultiplexer
): number {
  const channelKey = `tickers:${market}`;
  return multiplexer.broadcast(channelKey, message);
}

/**
 * Helper to send to private orders channel for specific user
 */
export function sendToOrdersChannel(
  userId: string,
  message: ServerMessage,
  multiplexer: ChannelMultiplexer
): number {
  // For private channels, we'd need to track user->connection mapping
  // For now, just broadcast to orders channel
  // In production, you'd filter by userId
  const channelKey = `orders`;
  return multiplexer.broadcast(channelKey, message);
}

/**
 * Helper to send to private balances channel for specific user
 */
export function sendToBalancesChannel(
  userId: string,
  message: ServerMessage,
  multiplexer: ChannelMultiplexer
): number {
  // For private channels, we'd need to track user->connection mapping
  // For now, just broadcast to balances channel
  // In production, you'd filter by userId
  const channelKey = `balances`;
  return multiplexer.broadcast(channelKey, message);
}
