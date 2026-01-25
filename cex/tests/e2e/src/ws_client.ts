/**
 * WebSocket Client for E2E Testing
 * 
 * Subscribes to real-time exchange data:
 * - Orderbook updates
 * - Trade streams
 * - User order updates
 * - Account balance updates
 * 
 * Handles reconnection, sequence gaps, and backpressure.
 */

import WebSocket from 'ws';
import { EventEmitter } from 'events';

export interface WSClientOptions {
  url: string;
  apiKey?: string;
  reconnect?: boolean;
  reconnectDelay?: number;
  maxReconnectAttempts?: number;
}

export interface WSMessage {
  type: string;
  channel: string;
  data: any;
  seq?: number;
  timestamp?: number;
}

export class WSClient extends EventEmitter {
  private url: string;
  private apiKey?: string;
  private reconnect: boolean;
  private reconnectDelay: number;
  private maxReconnectAttempts: number;
  
  private ws?: WebSocket;
  private connected = false;
  private reconnectAttempts = 0;
  private subscriptions = new Set<string>();
  private sequenceNumbers = new Map<string, number>();
  
  constructor(options: WSClientOptions) {
    super();
    this.url = options.url;
    this.apiKey = options.apiKey;
    this.reconnect = options.reconnect ?? true;
    this.reconnectDelay = options.reconnectDelay ?? 1000;
    this.maxReconnectAttempts = options.maxReconnectAttempts ?? 10;
  }
  
  /**
   * Connect to WebSocket
   */
  async connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(this.url);
        
        this.ws.on('open', () => {
          this.connected = true;
          this.reconnectAttempts = 0;
          this.emit('connected');
          
          // Resubscribe to channels
          this.subscriptions.forEach(channel => {
            this.send({
              type: 'subscribe',
              channel,
            });
          });
          
          resolve();
        });
        
        this.ws.on('message', (data: WebSocket.Data) => {
          try {
            const message: WSMessage = JSON.parse(data.toString());
            this.handleMessage(message);
          } catch (error) {
            this.emit('error', new Error(`Failed to parse message: ${error}`));
          }
        });
        
        this.ws.on('close', () => {
          this.connected = false;
          this.emit('disconnected');
          
          if (this.reconnect && this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            setTimeout(() => this.connect(), this.reconnectDelay);
          }
        });
        
        this.ws.on('error', (error) => {
          this.emit('error', error);
          reject(error);
        });
        
      } catch (error) {
        reject(error);
      }
    });
  }
  
  /**
   * Disconnect from WebSocket
   */
  disconnect(): void {
    this.reconnect = false;
    
    if (this.ws) {
      this.ws.close();
      this.ws = undefined;
    }
    
    this.connected = false;
  }
  
  /**
   * Subscribe to channel
   */
  subscribe(channel: string): void {
    this.subscriptions.add(channel);
    
    if (this.connected) {
      this.send({
        type: 'subscribe',
        channel,
      });
    }
  }
  
  /**
   * Unsubscribe from channel
   */
  unsubscribe(channel: string): void {
    this.subscriptions.delete(channel);
    
    if (this.connected) {
      this.send({
        type: 'unsubscribe',
        channel,
      });
    }
  }
  
  /**
   * Subscribe to orderbook updates
   */
  subscribeOrderbook(market: string): void {
    this.subscribe(`orderbook:${market}`);
  }
  
  /**
   * Subscribe to trade stream
   */
  subscribeTrades(market: string): void {
    this.subscribe(`trades:${market}`);
  }
  
  /**
   * Subscribe to user orders (requires auth)
   */
  subscribeOrders(): void {
    if (!this.apiKey) {
      throw new Error('API key required for private channels');
    }
    this.subscribe('user:orders');
  }
  
  /**
   * Subscribe to user balance updates (requires auth)
   */
  subscribeBalance(): void {
    if (!this.apiKey) {
      throw new Error('API key required for private channels');
    }
    this.subscribe('user:balance');
  }
  
  /**
   * Send message to server
   */
  private send(message: any): void {
    if (!this.ws || !this.connected) {
      throw new Error('WebSocket not connected');
    }
    
    // Add auth if available
    if (this.apiKey) {
      message.apiKey = this.apiKey;
    }
    
    this.ws.send(JSON.stringify(message));
  }
  
  /**
   * Handle incoming message
   */
  private handleMessage(message: WSMessage): void {
    const { type, channel, data, seq } = message;
    
    // Check for sequence gaps
    if (seq !== undefined && channel) {
      const lastSeq = this.sequenceNumbers.get(channel);
      
      if (lastSeq !== undefined && seq !== lastSeq + 1) {
        this.emit('sequence-gap', {
          channel,
          expected: lastSeq + 1,
          received: seq,
          gap: seq - lastSeq - 1,
        });
      }
      
      this.sequenceNumbers.set(channel, seq);
    }
    
    // Emit channel-specific events
    this.emit('message', message);
    this.emit(`message:${type}`, data);
    
    if (channel) {
      this.emit(`message:${channel}`, data);
    }
    
    // Specific message types
    switch (type) {
      case 'orderbook':
        this.emit('orderbook', { market: channel.split(':')[1], data });
        break;
        
      case 'trade':
        this.emit('trade', { market: channel.split(':')[1], data });
        break;
        
      case 'order':
        this.emit('order', data);
        break;
        
      case 'balance':
        this.emit('balance', data);
        break;
        
      case 'error':
        this.emit('ws-error', data);
        break;
    }
  }
  
  /**
   * Wait for specific message type
   */
  async waitFor(eventName: string, timeout = 5000): Promise<any> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.off(eventName, handler);
        reject(new Error(`Timeout waiting for ${eventName}`));
      }, timeout);
      
      const handler = (data: any) => {
        clearTimeout(timer);
        resolve(data);
      };
      
      this.once(eventName, handler);
    });
  }
  
  /**
   * Get connection status
   */
  isConnected(): boolean {
    return this.connected;
  }
  
  /**
   * Get subscription list
   */
  getSubscriptions(): string[] {
    return Array.from(this.subscriptions);
  }
  
  /**
   * Get metrics
   */
  getMetrics(): {
    connected: boolean;
    reconnectAttempts: number;
    subscriptions: number;
    channels: string[];
  } {
    return {
      connected: this.connected,
      reconnectAttempts: this.reconnectAttempts,
      subscriptions: this.subscriptions.size,
      channels: Array.from(this.subscriptions),
    };
  }
}

/**
 * Create multiple WS clients for load testing
 */
export class WSClientPool {
  private clients: WSClient[] = [];
  private options: WSClientOptions;
  
  constructor(options: WSClientOptions) {
    this.options = options;
  }
  
  /**
   * Create and connect N clients
   */
  async createClients(count: number): Promise<WSClient[]> {
    const clients: WSClient[] = [];
    
    for (let i = 0; i < count; i++) {
      const client = new WSClient(this.options);
      await client.connect();
      clients.push(client);
      this.clients.push(client);
    }
    
    return clients;
  }
  
  /**
   * Disconnect all clients
   */
  disconnectAll(): void {
    this.clients.forEach(client => client.disconnect());
    this.clients = [];
  }
  
  /**
   * Get all clients
   */
  getClients(): WSClient[] {
    return this.clients;
  }
  
  /**
   * Get aggregate metrics
   */
  getMetrics(): {
    totalClients: number;
    connected: number;
    totalSubscriptions: number;
    totalReconnects: number;
  } {
    return {
      totalClients: this.clients.length,
      connected: this.clients.filter(c => c.isConnected()).length,
      totalSubscriptions: this.clients.reduce((sum, c) => sum + c.getSubscriptions().length, 0),
      totalReconnects: this.clients.reduce((sum, c) => sum + c.getMetrics().reconnectAttempts, 0),
    };
  }
}
