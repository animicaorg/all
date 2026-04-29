import { wsMessageSchema, type WSMessage } from "./ws-types";

export type WSConnectionState = "connecting" | "connected" | "disconnected" | "reconnecting" | "error";

export interface WSClientOptions {
  url: string;
  userId?: string;
  onMessage?: (message: WSMessage) => void;
  onStateChange?: (state: WSConnectionState) => void;
  onError?: (error: Error) => void;
  reconnect?: boolean;
  reconnectInterval?: number;
  maxReconnectAttempts?: number;
  heartbeatInterval?: number;
  heartbeatTimeout?: number;
}

export class WSClient {
  private ws: WebSocket | null = null;
  private state: WSConnectionState = "disconnected";
  private options: Required<WSClientOptions>;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimeoutTimer: ReturnType<typeof setTimeout> | null = null;
  private lastPingTime = 0;
  private lastPongTime = 0;
  private subscriptions = new Set<string>();
  private pendingSubscriptions = new Set<string>();

  constructor(options: WSClientOptions) {
    this.options = {
      userId: options.userId || undefined,
      onMessage: options.onMessage || (() => {}),
      onStateChange: options.onStateChange || (() => {}),
      onError: options.onError || (() => {}),
      reconnect: options.reconnect !== false,
      reconnectInterval: options.reconnectInterval || 1000,
      maxReconnectAttempts: options.maxReconnectAttempts || 10,
      heartbeatInterval: options.heartbeatInterval || 30000,
      heartbeatTimeout: options.heartbeatTimeout || 10000,
      url: options.url,
    };
  }

  connect(): void {
    if (this.ws && this.state !== "disconnected") {
      return;
    }

    this.setState("connecting");
    this.reconnectAttempts++;

    try {
      const url = new URL(this.options.url);
      if (this.options.userId) {
        url.searchParams.set("userId", this.options.userId);
      }

      this.ws = new WebSocket(url.toString());

      this.ws.onopen = () => {
        this.setState("connected");
        this.reconnectAttempts = 0;
        this.lastPongTime = Date.now();
        this.startHeartbeat();
        this.resubscribe();
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          const message = wsMessageSchema.parse(data);

          if (message.type === "ping") {
            this.lastPingTime = message.timestamp;
            this.send({ action: "ping" });
          } else if (message.type === "pong") {
            this.lastPongTime = Date.now();
          } else {
            this.options.onMessage(message);
          }
        } catch (error) {
          console.error("Failed to parse WebSocket message:", error);
          this.options.onError(error instanceof Error ? error : new Error("Failed to parse message"));
        }
      };

      this.ws.onerror = (event) => {
        const error = new Error("WebSocket error");
        this.setState("error");
        console.warn("WebSocket transport error:", event);
        this.options.onError(error);
      };

      this.ws.onclose = () => {
        this.cleanup();
        this.setState("disconnected");

        if (
          this.options.reconnect &&
          this.reconnectAttempts < this.options.maxReconnectAttempts
        ) {
          this.scheduleReconnect();
        }
      };
    } catch (error) {
      console.error("Failed to create WebSocket:", error);
      this.setState("error");
      this.options.onError(error instanceof Error ? error : new Error("Failed to create WebSocket"));
    }
  }

  disconnect(): void {
    this.options.reconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.cleanup();
    this.setState("disconnected");
  }

  subscribe(channel: string, symbol?: string): void {
    const subKey = symbol ? `${channel}:${symbol}` : channel;

    if (this.subscriptions.has(subKey)) {
      return;
    }

    if (this.state === "connected") {
      this.send({ action: "subscribe", channel, symbol });
      this.subscriptions.add(subKey);
    } else {
      this.pendingSubscriptions.add(subKey);
    }
  }

  unsubscribe(channel: string, symbol?: string): void {
    const subKey = symbol ? `${channel}:${symbol}` : channel;

    if (this.state === "connected") {
      this.send({ action: "unsubscribe", channel, symbol });
    }

    this.subscriptions.delete(subKey);
    this.pendingSubscriptions.delete(subKey);
  }

  getState(): WSConnectionState {
    return this.state;
  }

  getStats() {
    return {
      state: this.state,
      reconnectAttempts: this.reconnectAttempts,
      lastPingTime: this.lastPingTime,
      lastPongTime: this.lastPongTime,
      subscriptions: Array.from(this.subscriptions),
      latency: this.lastPongTime - this.lastPingTime,
    };
  }

  private send(data: any): void {
    if (this.ws && this.state === "connected") {
      this.ws.send(JSON.stringify(data));
    }
  }

  private setState(state: WSConnectionState): void {
    this.state = state;
    this.options.onStateChange(state);
  }

  private startHeartbeat(): void {
    this.heartbeatTimer = setInterval(() => {
      if (this.state !== "connected") {
        return;
      }

      // Check if last pong is too old
      const now = Date.now();
      if (now - this.lastPongTime > this.options.heartbeatTimeout + this.options.heartbeatInterval) {
        console.warn("Heartbeat timeout - connection appears stale");
        this.ws?.close();
        return;
      }

      // Send ping
      this.send({ action: "ping" });
    }, this.options.heartbeatInterval);
  }

  private cleanup(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    if (this.heartbeatTimeoutTimer) {
      clearTimeout(this.heartbeatTimeoutTimer);
      this.heartbeatTimeoutTimer = null;
    }
  }

  private scheduleReconnect(): void {
    this.setState("reconnecting");

    // Exponential backoff with jitter
    const baseDelay = this.options.reconnectInterval;
    const exponentialDelay = baseDelay * Math.pow(2, Math.min(this.reconnectAttempts - 1, 5));
    const jitter = Math.random() * 1000;
    const delay = Math.min(exponentialDelay + jitter, 30000);

    console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts}/${this.options.maxReconnectAttempts})`);

    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  private resubscribe(): void {
    // Resubscribe to all active subscriptions
    this.subscriptions.forEach((subKey) => {
      const [channel, symbol] = subKey.split(":");
      this.send({ action: "subscribe", channel, symbol });
    });

    // Subscribe to pending subscriptions
    this.pendingSubscriptions.forEach((subKey) => {
      const [channel, symbol] = subKey.split(":");
      this.send({ action: "subscribe", channel, symbol });
      this.subscriptions.add(subKey);
    });

    this.pendingSubscriptions.clear();
  }
}
