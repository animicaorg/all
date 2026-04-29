import { create } from "zustand";
import { WSClient, WSConnectionState } from "./ws-client";
import { Orderbook, Trade, Ticker, WSMessage, OrderbookLevel } from "./ws-types";
import { getWsUrl } from "./endpoints";

const WS_URL = getWsUrl();

interface WSStore {
  // Connection state
  client: WSClient | null;
  connectionState: WSConnectionState;
  lastMessageTime: number;

  // Orderbook data
  orderbooks: Map<string, Orderbook>;

  // Trades data
  trades: Map<string, Trade[]>;

  // Ticker data
  tickers: Map<string, Ticker>;

  // Actions
  connect: (userId?: string) => void;
  disconnect: () => void;
  subscribe: (channel: string, symbol?: string) => void;
  unsubscribe: (channel: string, symbol?: string) => void;
  getStats: () => any;
}

export const useWSStore = create<WSStore>((set, get) => ({
  client: null,
  connectionState: "disconnected",
  lastMessageTime: 0,
  orderbooks: new Map(),
  trades: new Map(),
  tickers: new Map(),

  connect: (userId?: string) => {
    const existing = get().client;
    if (existing) {
      existing.disconnect();
    }

    const client = new WSClient({
      url: WS_URL,
      userId,
      onMessage: (message: WSMessage) => {
        set({ lastMessageTime: Date.now() });
        handleMessage(message, set, get);
      },
      onStateChange: (state: WSConnectionState) => {
        set({ connectionState: state });
      },
      onError: (error: Error) => {
        console.warn("WebSocket client error:", error.message);
      },
    });

    client.connect();
    set({ client });
  },

  disconnect: () => {
    const { client } = get();
    if (client) {
      client.disconnect();
    }
    set({
      client: null,
      connectionState: "disconnected",
      orderbooks: new Map(),
      trades: new Map(),
      tickers: new Map(),
    });
  },

  subscribe: (channel: string, symbol?: string) => {
    const { client } = get();
    if (client) {
      client.subscribe(channel, symbol);
    }
  },

  unsubscribe: (channel: string, symbol?: string) => {
    const { client } = get();
    if (client) {
      client.unsubscribe(channel, symbol);
    }
  },

  getStats: () => {
    const { client } = get();
    return client ? client.getStats() : null;
  },
}));

function handleMessage(message: WSMessage, set: any, get: any) {
  if (message.type === "snapshot" && message.channel === "orderbook") {
    const orderbook: Orderbook = {
      symbol: message.symbol,
      bids: message.data.bids.map(([price, quantity]) => ({ price, quantity })),
      asks: message.data.asks.map(([price, quantity]) => ({ price, quantity })),
      sequence: message.data.sequence,
    };

    const orderbooks = new Map(get().orderbooks);
    orderbooks.set(message.symbol, orderbook);
    set({ orderbooks });
  } else if (message.type === "update" && message.channel === "orderbook") {
    const orderbooks = new Map(get().orderbooks);
    const existing = orderbooks.get(message.symbol);

    if (!existing || (typeof message.data.sequence === 'number' && message.data.sequence <= existing.sequence)) {
      // Sequence gap or old message - ignore
      return;
    }

    // Apply delta update
    const updated: Orderbook = {
      symbol: message.symbol,
      bids: existing.bids,
      asks: existing.asks,
      sequence: message.data.sequence,
    };

    if (message.data.bids) {
      const bidsMap = new Map(existing.bids.map((b: OrderbookLevel) => [b.price, b.quantity]));
      message.data.bids.forEach(([price, quantity]) => {
        if (quantity === 0) {
          bidsMap.delete(price);
        } else {
          bidsMap.set(price, quantity);
        }
      });
      updated.bids = Array.from(bidsMap.entries())
        .map(([price, quantity]) => ({ price, quantity }))
        .sort((a, b) => b.price - a.price);
    }

    if (message.data.asks) {
      const asksMap = new Map(existing.asks.map((a: OrderbookLevel) => [a.price, a.quantity]));
      message.data.asks.forEach(([price, quantity]) => {
        if (quantity === 0) {
          asksMap.delete(price);
        } else {
          asksMap.set(price, quantity);
        }
      });
      updated.asks = Array.from(asksMap.entries())
        .map(([price, quantity]) => ({ price, quantity }))
        .sort((a, b) => a.price - b.price);
    }

    orderbooks.set(message.symbol, updated);
    set({ orderbooks });
  } else if (message.type === "snapshot" && message.channel === "trades") {
    const trades = new Map(get().trades);
    trades.set(message.symbol, message.data);
    set({ trades });
  } else if (message.type === "update" && message.channel === "trades") {
    const trades = new Map(get().trades);
    const existing = trades.get(message.symbol) || [];

    // Add new trade and deduplicate
    const updated = [message.data as Trade, ...existing];
    const seen = new Set<string>();
    const deduplicated = updated.filter((trade) => {
      if (seen.has(trade.id)) {
        return false;
      }
      seen.add(trade.id);
      return true;
    });

    // Keep only last 100 trades
    trades.set(message.symbol, deduplicated.slice(0, 100));
    set({ trades });
  } else if (message.type === "snapshot" && message.channel === "ticker") {
    const ticker: Ticker = {
      symbol: message.symbol,
      ...message.data,
    };

    const tickers = new Map(get().tickers);
    tickers.set(message.symbol, ticker);
    set({ tickers });
  } else if (message.type === "update" && message.channel === "ticker") {
    const tickers = new Map(get().tickers);
    const existing = tickers.get(message.symbol);

    if (existing) {
      const updated: Ticker = {
        symbol: message.symbol,
        lastPrice: message.data.lastPrice ?? existing.lastPrice,
        volume24h: message.data.volume24h ?? existing.volume24h,
        high24h: message.data.high24h ?? existing.high24h,
        low24h: message.data.low24h ?? existing.low24h,
        priceChange24h: message.data.priceChange24h ?? existing.priceChange24h,
      };
      tickers.set(message.symbol, updated);
      set({ tickers });
    }
  }
}
