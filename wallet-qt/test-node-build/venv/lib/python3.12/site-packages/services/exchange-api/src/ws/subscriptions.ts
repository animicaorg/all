/**
 * Subscription Management
 * 
 * Manages channel subscriptions for WebSocket connections with validation,
 * limits, and authentication requirements for private channels.
 */

import type { Logger } from '../utils/logger.js';
import type { Config } from '../config.js';
import type {
  ChannelConfig,
  SubscribeMessage,
  UnsubscribeMessage,
} from './protocol.js';
import {
  CHANNEL_TYPES,
  PRIVATE_CHANNELS,
  buildChannelKey,
  ERROR_CODES,
} from './protocol.js';

export interface SubscriptionResult {
  success: boolean;
  subscribed?: ChannelConfig[];
  failed?: Array<{ channel: ChannelConfig; reason: string }>;
  error?: string;
}

export interface UnsubscriptionResult {
  success: boolean;
  unsubscribed?: ChannelConfig[];
}

/**
 * Per-connection subscription state
 */
export class ConnectionSubscriptions {
  private subscriptions: Set<string> = new Set();
  private readonly connectionId: string;
  private readonly maxSubscriptions: number;
  private readonly logger: Logger;

  constructor(connectionId: string, config: Config, logger: Logger) {
    this.connectionId = connectionId;
    this.maxSubscriptions = config.WS_MAX_SUBSCRIPTIONS_PER_CLIENT;
    this.logger = logger;
  }

  /**
   * Get all active subscription keys
   */
  getAll(): Set<string> {
    return new Set(this.subscriptions);
  }

  /**
   * Check if subscribed to a channel
   */
  has(channelKey: string): boolean {
    return this.subscriptions.has(channelKey);
  }

  /**
   * Get subscription count
   */
  count(): number {
    return this.subscriptions.size;
  }

  /**
   * Add a subscription
   * @returns true if added, false if already exists or limit reached
   */
  add(channelKey: string): boolean {
    if (this.subscriptions.has(channelKey)) {
      return false;
    }

    if (this.subscriptions.size >= this.maxSubscriptions) {
      this.logger.warn(
        { connectionId: this.connectionId, channelKey },
        'Max subscriptions limit reached'
      );
      return false;
    }

    this.subscriptions.add(channelKey);
    this.logger.debug(
      { connectionId: this.connectionId, channelKey, total: this.subscriptions.size },
      'Subscription added'
    );
    return true;
  }

  /**
   * Remove a subscription
   * @returns true if removed, false if didn't exist
   */
  remove(channelKey: string): boolean {
    const removed = this.subscriptions.delete(channelKey);
    if (removed) {
      this.logger.debug(
        { connectionId: this.connectionId, channelKey, total: this.subscriptions.size },
        'Subscription removed'
      );
    }
    return removed;
  }

  /**
   * Clear all subscriptions
   */
  clear(): void {
    const count = this.subscriptions.size;
    this.subscriptions.clear();
    this.logger.debug(
      { connectionId: this.connectionId, count },
      'All subscriptions cleared'
    );
  }
}

/**
 * Global subscription manager - tracks which connections are subscribed to which channels
 */
export class SubscriptionManager {
  // Map: channelKey -> Set<connectionId>
  private readonly channelSubscribers: Map<string, Set<string>> = new Map();
  private readonly logger: Logger;

  constructor(logger: Logger) {
    this.logger = logger;
  }

  /**
   * Add a connection to a channel's subscriber list
   */
  addSubscriber(channelKey: string, connectionId: string): void {
    let subscribers = this.channelSubscribers.get(channelKey);
    if (!subscribers) {
      subscribers = new Set();
      this.channelSubscribers.set(channelKey, subscribers);
    }
    subscribers.add(connectionId);

    this.logger.debug(
      { channelKey, connectionId, subscriberCount: subscribers.size },
      'Subscriber added to channel'
    );
  }

  /**
   * Remove a connection from a channel's subscriber list
   */
  removeSubscriber(channelKey: string, connectionId: string): void {
    const subscribers = this.channelSubscribers.get(channelKey);
    if (subscribers) {
      subscribers.delete(connectionId);
      if (subscribers.size === 0) {
        this.channelSubscribers.delete(channelKey);
        this.logger.debug({ channelKey }, 'Channel removed (no subscribers)');
      } else {
        this.logger.debug(
          { channelKey, connectionId, subscriberCount: subscribers.size },
          'Subscriber removed from channel'
        );
      }
    }
  }

  /**
   * Remove a connection from all channels
   */
  removeSubscriberFromAll(connectionId: string): void {
    let removed = 0;
    for (const [channelKey, subscribers] of this.channelSubscribers.entries()) {
      if (subscribers.has(connectionId)) {
        subscribers.delete(connectionId);
        removed++;
        if (subscribers.size === 0) {
          this.channelSubscribers.delete(channelKey);
        }
      }
    }
    this.logger.debug({ connectionId, channelsRemoved: removed }, 'Subscriber removed from all channels');
  }

  /**
   * Get all connection IDs subscribed to a channel
   */
  getSubscribers(channelKey: string): Set<string> {
    return this.channelSubscribers.get(channelKey) || new Set();
  }

  /**
   * Get total number of active channels
   */
  getChannelCount(): number {
    return this.channelSubscribers.size;
  }

  /**
   * Get statistics
   */
  getStats(): { channels: number; totalSubscriptions: number } {
    let totalSubscriptions = 0;
    for (const subscribers of this.channelSubscribers.values()) {
      totalSubscriptions += subscribers.size;
    }
    return {
      channels: this.channelSubscribers.size,
      totalSubscriptions,
    };
  }
}

/**
 * Validates a channel configuration
 */
export function validateChannel(
  channel: ChannelConfig,
  isAuthenticated: boolean
): { valid: boolean; reason?: string } {
  // Check channel name
  const validChannels = Object.values(CHANNEL_TYPES);
  if (!validChannels.includes(channel.name as any)) {
    return { valid: false, reason: 'Invalid channel name' };
  }

  // Check if private channel requires auth
  if (PRIVATE_CHANNELS.includes(channel.name as any) && !isAuthenticated) {
    return { valid: false, reason: 'Authentication required for private channel' };
  }

  // Market-specific channels require market parameter
  if (
    [CHANNEL_TYPES.BOOK, CHANNEL_TYPES.TRADES, CHANNEL_TYPES.TICKERS].includes(
      channel.name as any
    )
  ) {
    if (!channel.market) {
      return { valid: false, reason: 'Market parameter required' };
    }
  }

  // Candles channel requires both market and interval
  if (channel.name === CHANNEL_TYPES.CANDLES) {
    if (!channel.market || !channel.interval) {
      return { valid: false, reason: 'Market and interval parameters required for candles' };
    }
  }

  return { valid: true };
}

/**
 * Handles subscription request
 */
export function handleSubscribe(
  msg: SubscribeMessage,
  connectionSubs: ConnectionSubscriptions,
  globalManager: SubscriptionManager,
  connectionId: string,
  isAuthenticated: boolean,
  logger: Logger
): SubscriptionResult {
  const subscribed: ChannelConfig[] = [];
  const failed: Array<{ channel: ChannelConfig; reason: string }> = [];

  for (const channel of msg.channels) {
    // Validate channel
    const validation = validateChannel(channel, isAuthenticated);
    if (!validation.valid) {
      failed.push({ channel, reason: validation.reason! });
      continue;
    }

    const channelKey = buildChannelKey(channel);

    // Check if already subscribed
    if (connectionSubs.has(channelKey)) {
      logger.debug({ connectionId, channelKey }, 'Already subscribed, skipping');
      subscribed.push(channel); // Still return as subscribed
      continue;
    }

    // Try to add subscription
    if (connectionSubs.add(channelKey)) {
      globalManager.addSubscriber(channelKey, connectionId);
      subscribed.push(channel);
    } else {
      failed.push({
        channel,
        reason: 'Max subscriptions limit reached',
      });
    }
  }

  if (failed.length > 0) {
    logger.warn(
      { connectionId, subscribed: subscribed.length, failed: failed.length },
      'Some subscriptions failed'
    );
  }

  return {
    success: subscribed.length > 0,
    subscribed,
    failed: failed.length > 0 ? failed : undefined,
  };
}

/**
 * Handles unsubscription request
 */
export function handleUnsubscribe(
  msg: UnsubscribeMessage,
  connectionSubs: ConnectionSubscriptions,
  globalManager: SubscriptionManager,
  connectionId: string,
  logger: Logger
): UnsubscriptionResult {
  const unsubscribed: ChannelConfig[] = [];

  for (const channel of msg.channels) {
    const channelKey = buildChannelKey(channel);

    if (connectionSubs.remove(channelKey)) {
      globalManager.removeSubscriber(channelKey, connectionId);
      unsubscribed.push(channel);
    } else {
      logger.debug({ connectionId, channelKey }, 'Not subscribed, skipping');
    }
  }

  return {
    success: true,
    unsubscribed,
  };
}

/**
 * Cleans up all subscriptions for a connection
 */
export function cleanupSubscriptions(
  connectionSubs: ConnectionSubscriptions,
  globalManager: SubscriptionManager,
  connectionId: string,
  logger: Logger
): void {
  const channelKeys = Array.from(connectionSubs.getAll());

  for (const channelKey of channelKeys) {
    globalManager.removeSubscriber(channelKey, connectionId);
  }

  connectionSubs.clear();

  logger.debug(
    { connectionId, cleanedChannels: channelKeys.length },
    'Subscriptions cleaned up'
  );
}
