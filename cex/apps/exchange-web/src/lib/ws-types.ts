import { z } from "zod";

// WebSocket message schemas
const welcomeMessageSchema = z.object({
  type: z.literal("welcome"),
  message: z.string(),
  timestamp: z.number(),
});

const pingMessageSchema = z.object({
  type: z.literal("ping"),
  timestamp: z.number(),
});

const pongMessageSchema = z.object({
  type: z.literal("pong"),
  timestamp: z.number(),
});

const subscribedMessageSchema = z.object({
  type: z.literal("subscribed"),
  channel: z.string(),
  symbol: z.string().optional(),
  timestamp: z.number(),
});

const unsubscribedMessageSchema = z.object({
  type: z.literal("unsubscribed"),
  channel: z.string(),
  symbol: z.string().optional(),
  timestamp: z.number(),
});

const orderbookSnapshotSchema = z.object({
  type: z.literal("snapshot"),
  channel: z.literal("orderbook"),
  symbol: z.string(),
  data: z.object({
    bids: z.array(z.tuple([z.number(), z.number()])),
    asks: z.array(z.tuple([z.number(), z.number()])),
    sequence: z.number(),
  }),
  timestamp: z.number(),
});

const orderbookUpdateSchema = z.object({
  type: z.literal("update"),
  channel: z.literal("orderbook"),
  symbol: z.string(),
  data: z.object({
    bids: z.array(z.tuple([z.number(), z.number()])).optional(),
    asks: z.array(z.tuple([z.number(), z.number()])).optional(),
    sequence: z.number(),
  }),
  timestamp: z.number(),
});

const tradesSnapshotSchema = z.object({
  type: z.literal("snapshot"),
  channel: z.literal("trades"),
  symbol: z.string(),
  data: z.array(
    z.object({
      id: z.string(),
      price: z.number(),
      quantity: z.number(),
      side: z.enum(["buy", "sell"]),
      sequence: z.number(),
      timestamp: z.number(),
    })
  ),
  timestamp: z.number(),
});

const tradesUpdateSchema = z.object({
  type: z.literal("update"),
  channel: z.literal("trades"),
  symbol: z.string(),
  data: z.object({
    id: z.string(),
    price: z.number(),
    quantity: z.number(),
    side: z.enum(["buy", "sell"]),
    sequence: z.number(),
    timestamp: z.number(),
  }),
  timestamp: z.number(),
});

const tickerSnapshotSchema = z.object({
  type: z.literal("snapshot"),
  channel: z.literal("ticker"),
  symbol: z.string(),
  data: z.object({
    lastPrice: z.number(),
    volume24h: z.number(),
    high24h: z.number(),
    low24h: z.number(),
    priceChange24h: z.number(),
  }),
  timestamp: z.number(),
});

const tickerUpdateSchema = z.object({
  type: z.literal("update"),
  channel: z.literal("ticker"),
  symbol: z.string(),
  data: z.object({
    lastPrice: z.number().optional(),
    volume24h: z.number().optional(),
    high24h: z.number().optional(),
    low24h: z.number().optional(),
    priceChange24h: z.number().optional(),
  }),
  timestamp: z.number(),
});

const errorMessageSchema = z.object({
  type: z.literal("error"),
  message: z.string(),
});

export const wsMessageSchema = z.union([
  welcomeMessageSchema,
  pingMessageSchema,
  pongMessageSchema,
  subscribedMessageSchema,
  unsubscribedMessageSchema,
  orderbookSnapshotSchema,
  orderbookUpdateSchema,
  tradesSnapshotSchema,
  tradesUpdateSchema,
  tickerSnapshotSchema,
  tickerUpdateSchema,
  errorMessageSchema]);

export type WSMessage = z.infer<typeof wsMessageSchema>;
export type OrderbookSnapshot = z.infer<typeof orderbookSnapshotSchema>;
export type OrderbookUpdate = z.infer<typeof orderbookUpdateSchema>;
export type TradesSnapshot = z.infer<typeof tradesSnapshotSchema>;
export type TradesUpdate = z.infer<typeof tradesUpdateSchema>;
export type TickerSnapshot = z.infer<typeof tickerSnapshotSchema>;
export type TickerUpdate = z.infer<typeof tickerUpdateSchema>;

// Orderbook data structure
export interface OrderbookLevel {
  price: number;
  quantity: number;
}

export interface Orderbook {
  bids: OrderbookLevel[];
  asks: OrderbookLevel[];
  sequence: number;
  symbol: string;
}

// Trade data structure
export interface Trade {
  id: string;
  price: number;
  quantity: number;
  side: "buy" | "sell";
  sequence: number;
  timestamp: number;
}

// Ticker data structure
export interface Ticker {
  symbol: string;
  lastPrice: number;
  volume24h: number;
  high24h: number;
  low24h: number;
  priceChange24h: number;
}
