/**
 * Heartbeat Mechanism
 * 
 * Manages ping/pong heartbeats to detect disconnected clients and
 * keep connections alive through firewalls and proxies.
 */

import type { Logger } from '../utils/logger.js';
import type { Config } from '../config.js';
import type { PongMessage } from './protocol.js';

export interface HeartbeatState {
  lastPing: number;
  lastPong: number;
  isAlive: boolean;
  missedPongs: number;
}

/**
 * Heartbeat manager for tracking connection liveness
 */
export class HeartbeatManager {
  private readonly heartbeatInterval: number;
  private readonly heartbeatTimeout: number;
  private readonly logger: Logger;
  private states: Map<string, HeartbeatState> = new Map();
  private intervalHandle?: NodeJS.Timeout;

  constructor(config: Config, logger: Logger) {
    this.heartbeatInterval = config.WS_HEARTBEAT_INTERVAL_MS;
    this.heartbeatTimeout = config.WS_HEARTBEAT_TIMEOUT_MS;
    this.logger = logger;
  }

  /**
   * Register a new connection
   */
  register(connectionId: string): void {
    const now = Date.now();
    this.states.set(connectionId, {
      lastPing: now,
      lastPong: now,
      isAlive: true,
      missedPongs: 0,
    });

    this.logger.debug({ connectionId }, 'Heartbeat registered');
  }

  /**
   * Unregister a connection
   */
  unregister(connectionId: string): void {
    this.states.delete(connectionId);
    this.logger.debug({ connectionId }, 'Heartbeat unregistered');
  }

  /**
   * Mark that we sent a ping to a connection
   */
  markPingSent(connectionId: string): void {
    const state = this.states.get(connectionId);
    if (state) {
      state.lastPing = Date.now();
      state.isAlive = false; // Wait for pong to mark alive
    }
  }

  /**
   * Mark that we received a pong from a connection
   */
  markPongReceived(connectionId: string): void {
    const state = this.states.get(connectionId);
    if (state) {
      state.lastPong = Date.now();
      state.isAlive = true;
      state.missedPongs = 0;

      this.logger.trace({ connectionId }, 'Pong received');
    }
  }

  /**
   * Check if connection is alive
   */
  isAlive(connectionId: string): boolean {
    const state = this.states.get(connectionId);
    if (!state) {
      return false;
    }

    const now = Date.now();
    const timeSinceLastPong = now - state.lastPong;

    return timeSinceLastPong < this.heartbeatTimeout;
  }

  /**
   * Get connections that should be terminated (no pong within timeout)
   */
  getDeadConnections(): string[] {
    const dead: string[] = [];
    const now = Date.now();

    for (const [connectionId, state] of this.states.entries()) {
      const timeSinceLastPong = now - state.lastPong;
      const timeSinceLastPing = now - state.lastPing;

      // Only check if we've sent a ping recently
      if (
        timeSinceLastPing < this.heartbeatTimeout &&
        timeSinceLastPong >= this.heartbeatTimeout
      ) {
        dead.push(connectionId);
        this.logger.warn(
          {
            connectionId,
            timeSinceLastPong,
            missedPongs: state.missedPongs,
          },
          'Connection appears dead'
        );
      }
    }

    return dead;
  }

  /**
   * Get all connection IDs that need a ping
   */
  getConnectionsNeedingPing(): string[] {
    const needPing: string[] = [];
    const now = Date.now();

    for (const [connectionId, state] of this.states.entries()) {
      const timeSinceLastPing = now - state.lastPing;

      if (timeSinceLastPing >= this.heartbeatInterval) {
        needPing.push(connectionId);
      }
    }

    return needPing;
  }

  /**
   * Get heartbeat statistics
   */
  getStats(): {
    total: number;
    alive: number;
    dead: number;
    avgResponseTime: number;
  } {
    let alive = 0;
    let dead = 0;
    let totalResponseTime = 0;
    let responseSamples = 0;

    for (const [, state] of this.states.entries()) {
      if (this.isAlive('')) {
        // Use helper method logic
        const now = Date.now();
        if (now - state.lastPong < this.heartbeatTimeout) {
          alive++;
        } else {
          dead++;
        }
      }

      // Calculate response time if we have both ping and pong
      if (state.lastPong >= state.lastPing) {
        totalResponseTime += state.lastPong - state.lastPing;
        responseSamples++;
      }
    }

    return {
      total: this.states.size,
      alive,
      dead,
      avgResponseTime: responseSamples > 0 ? totalResponseTime / responseSamples : 0,
    };
  }

  /**
   * Clear all state
   */
  clear(): void {
    this.states.clear();
  }
}

/**
 * Creates a ping message
 */
export function createPingMessage(): string {
  return JSON.stringify({ op: 'ping', ts: Date.now() });
}

/**
 * Creates a pong message
 */
export function createPongMessage(clientTimestamp?: number): PongMessage {
  return {
    op: 'pong',
    ts: clientTimestamp || Date.now(),
  };
}

/**
 * Starts the heartbeat checker interval
 * 
 * @param manager - Heartbeat manager instance
 * @param sendPing - Callback to send ping to a connection
 * @param terminateConnection - Callback to terminate a dead connection
 * @param logger - Logger instance
 * @returns Interval handle (use clearInterval to stop)
 */
export function startHeartbeatChecker(
  manager: HeartbeatManager,
  sendPing: (connectionId: string) => void,
  terminateConnection: (connectionId: string, reason: string) => void,
  logger: Logger
): NodeJS.Timeout {
  const interval = setInterval(() => {
    try {
      // Check for dead connections
      const dead = manager.getDeadConnections();
      for (const connectionId of dead) {
        logger.info({ connectionId }, 'Terminating dead connection');
        terminateConnection(connectionId, 'Heartbeat timeout');
      }

      // Send pings to connections that need them
      const needPing = manager.getConnectionsNeedingPing();
      for (const connectionId of needPing) {
        manager.markPingSent(connectionId);
        sendPing(connectionId);
        logger.trace({ connectionId }, 'Ping sent');
      }

      // Log stats periodically
      if (Math.random() < 0.1) {
        // 10% chance to log stats
        const stats = manager.getStats();
        logger.debug({ stats }, 'Heartbeat stats');
      }
    } catch (error) {
      logger.error({ error }, 'Error in heartbeat checker');
    }
  }, 5000); // Check every 5 seconds

  return interval;
}

/**
 * Stops the heartbeat checker
 */
export function stopHeartbeatChecker(interval: NodeJS.Timeout): void {
  clearInterval(interval);
}
