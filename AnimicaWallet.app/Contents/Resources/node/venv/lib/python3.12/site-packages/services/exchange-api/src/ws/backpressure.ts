/**
 * Backpressure and Queue Management
 * 
 * Manages per-connection outgoing message queues with priority-based
 * dropping when queue limits are exceeded.
 */

import type { Logger } from '../utils/logger.js';
import type { Config } from '../config.js';
import type { ServerMessage } from './protocol.js';
import { MessagePriority, getMessagePriority, WS_CLOSE_CODES } from './protocol.js';

export interface QueuedMessage {
  message: ServerMessage;
  priority: MessagePriority;
  timestamp: number;
}

export interface QueueStats {
  size: number;
  dropped: number;
  sent: number;
  oldestMessageAge: number;
}

/**
 * Per-connection outgoing message queue with backpressure handling
 */
export class MessageQueue {
  private queue: QueuedMessage[] = [];
  private readonly maxSize: number;
  private droppedCount: number = 0;
  private sentCount: number = 0;
  private readonly connectionId: string;
  private readonly logger: Logger;

  constructor(connectionId: string, maxSize: number, logger: Logger) {
    this.connectionId = connectionId;
    this.maxSize = maxSize;
    this.logger = logger;
  }

  /**
   * Enqueue a message with priority-based backpressure
   * @returns true if enqueued, false if dropped
   */
  enqueue(message: ServerMessage): boolean {
    const priority = getMessagePriority(message);
    const queuedMessage: QueuedMessage = {
      message,
      priority,
      timestamp: Date.now(),
    };

    // If queue is not full, just add it
    if (this.queue.length < this.maxSize) {
      this.queue.push(queuedMessage);
      return true;
    }

    // Queue is full - apply backpressure strategy
    return this.applyBackpressure(queuedMessage);
  }

  /**
   * Apply backpressure when queue is full
   * Strategy: Drop lower priority messages first
   */
  private applyBackpressure(newMessage: QueuedMessage): boolean {
    // Find lowest priority message in queue that's lower than new message
    let lowestPriorityIdx = -1;
    let lowestPriority = newMessage.priority;

    for (let i = 0; i < this.queue.length; i++) {
      if (this.queue[i].priority > lowestPriority) {
        lowestPriority = this.queue[i].priority;
        lowestPriorityIdx = i;
      }
    }

    // If we found a lower priority message, drop it and add new one
    if (lowestPriorityIdx !== -1) {
      const dropped = this.queue.splice(lowestPriorityIdx, 1)[0];
      this.droppedCount++;
      this.logger.debug(
        {
          connectionId: this.connectionId,
          droppedPriority: dropped.priority,
          newPriority: newMessage.priority,
          queueSize: this.queue.length,
        },
        'Dropped lower priority message due to backpressure'
      );
      this.queue.push(newMessage);
      return true;
    }

    // New message has lowest priority - drop it
    this.droppedCount++;
    this.logger.debug(
      {
        connectionId: this.connectionId,
        priority: newMessage.priority,
        queueSize: this.queue.length,
      },
      'Dropped new message due to backpressure (lowest priority)'
    );
    return false;
  }

  /**
   * Dequeue next message (FIFO within priority)
   */
  dequeue(): QueuedMessage | null {
    if (this.queue.length === 0) {
      return null;
    }

    // Sort by priority (lower number = higher priority), then by timestamp
    this.queue.sort((a, b) => {
      if (a.priority !== b.priority) {
        return a.priority - b.priority;
      }
      return a.timestamp - b.timestamp;
    });

    const message = this.queue.shift();
    if (message) {
      this.sentCount++;
      return message;
    }

    return null;
  }

  /**
   * Peek at next message without removing it
   */
  peek(): QueuedMessage | null {
    if (this.queue.length === 0) {
      return null;
    }

    // Find highest priority message
    let highestPriority = this.queue[0].priority;
    let highestIdx = 0;

    for (let i = 1; i < this.queue.length; i++) {
      if (this.queue[i].priority < highestPriority) {
        highestPriority = this.queue[i].priority;
        highestIdx = i;
      }
    }

    return this.queue[highestIdx];
  }

  /**
   * Get queue size
   */
  size(): number {
    return this.queue.length;
  }

  /**
   * Check if queue is full
   */
  isFull(): boolean {
    return this.queue.length >= this.maxSize;
  }

  /**
   * Check if queue is empty
   */
  isEmpty(): boolean {
    return this.queue.length === 0;
  }

  /**
   * Check if queue is critically full (>90% capacity)
   */
  isCritical(): boolean {
    return this.queue.length >= this.maxSize * 0.9;
  }

  /**
   * Clear the queue
   */
  clear(): void {
    this.queue = [];
  }

  /**
   * Get queue statistics
   */
  getStats(): QueueStats {
    let oldestMessageAge = 0;
    if (this.queue.length > 0) {
      const now = Date.now();
      const oldestTimestamp = Math.min(...this.queue.map(m => m.timestamp));
      oldestMessageAge = now - oldestTimestamp;
    }

    return {
      size: this.queue.length,
      dropped: this.droppedCount,
      sent: this.sentCount,
      oldestMessageAge,
    };
  }
}

/**
 * Queue manager for all connections
 */
export class QueueManager {
  private queues: Map<string, MessageQueue> = new Map();
  private readonly config: Config;
  private readonly logger: Logger;

  constructor(config: Config, logger: Logger) {
    this.config = config;
    this.logger = logger;
  }

  /**
   * Create a queue for a connection
   */
  createQueue(connectionId: string): MessageQueue {
    const queue = new MessageQueue(
      connectionId,
      this.config.WS_MAX_OUTGOING_QUEUE_SIZE,
      this.logger
    );
    this.queues.set(connectionId, queue);
    this.logger.debug({ connectionId }, 'Message queue created');
    return queue;
  }

  /**
   * Get queue for a connection
   */
  getQueue(connectionId: string): MessageQueue | undefined {
    return this.queues.get(connectionId);
  }

  /**
   * Remove queue for a connection
   */
  removeQueue(connectionId: string): void {
    const queue = this.queues.get(connectionId);
    if (queue) {
      const stats = queue.getStats();
      this.logger.debug({ connectionId, stats }, 'Message queue removed');
      this.queues.delete(connectionId);
    }
  }

  /**
   * Get aggregate statistics across all queues
   */
  getAggregateStats(): {
    totalQueues: number;
    totalMessages: number;
    totalDropped: number;
    totalSent: number;
    criticalQueues: number;
  } {
    let totalMessages = 0;
    let totalDropped = 0;
    let totalSent = 0;
    let criticalQueues = 0;

    for (const queue of this.queues.values()) {
      const stats = queue.getStats();
      totalMessages += stats.size;
      totalDropped += stats.dropped;
      totalSent += stats.sent;
      if (queue.isCritical()) {
        criticalQueues++;
      }
    }

    return {
      totalQueues: this.queues.size,
      totalMessages,
      totalDropped,
      totalSent,
      criticalQueues,
    };
  }

  /**
   * Clear all queues
   */
  clear(): void {
    this.queues.clear();
  }
}

/**
 * Check if connection should be disconnected due to persistent backpressure
 * 
 * Disconnects if queue has been critically full for too long
 */
export function shouldDisconnectForBackpressure(
  queue: MessageQueue,
  criticalDurationMs: number = 30000 // 30 seconds
): boolean {
  if (!queue.isCritical()) {
    return false;
  }

  const stats = queue.getStats();
  
  // If oldest message is older than threshold and queue is critical, disconnect
  return stats.oldestMessageAge > criticalDurationMs;
}

/**
 * Get backpressure status message for logging
 */
export function getBackpressureStatus(queue: MessageQueue): string {
  const stats = queue.getStats();
  const utilization = (stats.size / 1000) * 100; // Assuming max 1000

  if (utilization < 50) {
    return 'normal';
  } else if (utilization < 75) {
    return 'moderate';
  } else if (utilization < 90) {
    return 'high';
  } else {
    return 'critical';
  }
}
