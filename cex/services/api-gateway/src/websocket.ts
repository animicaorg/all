import { WebSocketServer, WebSocket } from "ws";
import { IncomingMessage, Server as HttpServer } from "http";
import { Pool } from "pg";
import { NatsConnection } from "nats";
import { z } from "zod";

const subscribeSchema = z.object({
  action: z.literal("subscribe"),
  channel: z.enum(["orderbook", "trades", "ticker", "user_orders", "user_trades"]),
  symbol: z.string().optional(),
});

const unsubscribeSchema = z.object({
  action: z.literal("unsubscribe"),
  channel: z.enum(["orderbook", "trades", "ticker", "user_orders", "user_trades"]),
  symbol: z.string().optional(),
});

const pingSchema = z.object({
  action: z.literal("ping"),
});

const messageSchema = z.discriminatedUnion("action", [
  subscribeSchema,
  unsubscribeSchema,
  pingSchema,
]);

interface Client {
  ws: WebSocket;
  userId?: string;
  subscriptions: Set<string>;
  lastPing: number;
  lastPong: number;
}

export function createWebSocketServer(
  httpServer: HttpServer,
  pgPool: Pool,
  nats: NatsConnection
) {
  const wss = new WebSocketServer({ server: httpServer, path: "/ws" });
  const clients = new Map<WebSocket, Client>();
  const marketSymbolById = new Map<string, string>();
  const natsSubscriptions = [
    nats.subscribe("cex.order.event.*"),
    nats.subscribe("cex.trade.event.*"),
  ];

  void pumpMarketEvents(natsSubscriptions[0], "order");
  void pumpMarketEvents(natsSubscriptions[1], "trade");

  // Heartbeat interval - ping clients every 30 seconds
  const heartbeatInterval = setInterval(() => {
    const now = Date.now();
    clients.forEach((client, ws) => {
      // Close stale connections (no pong in 60 seconds)
      if (now - client.lastPong > 60000) {
        ws.terminate();
        return;
      }

      // Send ping
      if (ws.readyState === WebSocket.OPEN) {
        client.lastPing = now;
        ws.send(JSON.stringify({ type: "ping", timestamp: now }));
      }
    });
  }, 30000);

  wss.on("connection", (ws: WebSocket, req: IncomingMessage) => {
    // ⚠️ SECURITY WARNING: userId from query params is NOT secure!
    // This is for development only. In production, validate JWT tokens
    // TODO: Implement proper JWT authentication before production deployment
    const url = new URL(req.url || "", `http://${req.headers.host}`);
    const userId = url.searchParams.get("userId") || undefined;

    const client: Client = {
      ws,
      userId,
      subscriptions: new Set(),
      lastPing: Date.now(),
      lastPong: Date.now(),
    };

    clients.set(ws, client);

    // Send welcome message
    ws.send(
      JSON.stringify({
        type: "welcome",
        message: "Connected to Animica CEX WebSocket",
        timestamp: Date.now(),
      })
    );

    ws.on("message", async (data: Buffer) => {
      try {
        const message = JSON.parse(data.toString());
        const parsed = messageSchema.parse(message);

        if (parsed.action === "ping") {
          client.lastPong = Date.now();
          ws.send(JSON.stringify({ type: "pong", timestamp: Date.now() }));
          return;
        }

        if (parsed.action === "subscribe") {
          await handleSubscribe(client, parsed);
        } else if (parsed.action === "unsubscribe") {
          handleUnsubscribe(client, parsed);
        }
      } catch (error: any) {
        ws.send(
          JSON.stringify({
            type: "error",
            message: error.message || "Invalid message",
          })
        );
      }
    });

    ws.on("pong", () => {
      client.lastPong = Date.now();
    });

    ws.on("close", () => {
      clients.delete(ws);
    });

    ws.on("error", (error) => {
      console.error("WebSocket error:", error);
      clients.delete(ws);
    });
  });

  async function handleSubscribe(
    client: Client,
    message: z.infer<typeof subscribeSchema>
  ) {
    const { channel, symbol } = message;
    const subKey = symbol ? `${channel}:${symbol}` : channel;

    // Check subscription limit
    if (client.subscriptions.size >= 20) {
      client.ws.send(
        JSON.stringify({
          type: "error",
          message: "Maximum subscriptions reached (20)",
        })
      );
      return;
    }

    // User channels require authentication
    if (
      (channel === "user_orders" || channel === "user_trades") &&
      !client.userId
    ) {
      client.ws.send(
        JSON.stringify({
          type: "error",
          message: "Authentication required for user channels",
        })
      );
      return;
    }

    client.subscriptions.add(subKey);

    // Send snapshot
    if (channel === "orderbook" && symbol) {
      await sendOrderbookSnapshot(client, symbol);
    } else if (channel === "trades" && symbol) {
      await sendTradesSnapshot(client, symbol);
    } else if (channel === "ticker" && symbol) {
      await sendTickerSnapshot(client, symbol);
    }

    client.ws.send(
      JSON.stringify({
        type: "subscribed",
        channel,
        symbol,
        timestamp: Date.now(),
      })
    );
  }

  function handleUnsubscribe(
    client: Client,
    message: z.infer<typeof unsubscribeSchema>
  ) {
    const { channel, symbol } = message;
    const subKey = symbol ? `${channel}:${symbol}` : channel;

    client.subscriptions.delete(subKey);

    client.ws.send(
      JSON.stringify({
        type: "unsubscribed",
        channel,
        symbol,
        timestamp: Date.now(),
      })
    );
  }

  async function sendOrderbookSnapshot(client: Client, symbol: string) {
    try {
      // Get market ID
      const marketResult = await pgPool.query(
        "SELECT id FROM markets WHERE symbol = $1 AND active = true",
        [symbol]
      );

      if (marketResult.rows.length === 0) {
        return;
      }

      const marketId = marketResult.rows[0].id;

      // Get bids and asks
      const ordersResult = await pgPool.query(
        `
        SELECT 
          side,
          price,
          SUM(remaining_quantity) as total_quantity
        FROM orders
        WHERE market_id = $1
          AND status IN ('ACCEPTED', 'PARTIAL_FILL')
          AND remaining_quantity > 0
        GROUP BY side, price
        ORDER BY 
          CASE WHEN lower(side) = 'buy' THEN price END DESC,
          CASE WHEN lower(side) = 'sell' THEN price END ASC
        LIMIT 40
      `,
        [marketId]
      );

      const bids: Array<[number, number]> = [];
      const asks: Array<[number, number]> = [];

      ordersResult.rows.forEach((row: any) => {
        const price = parseFloat(row.price);
        const quantity = parseFloat(row.total_quantity);

        if (String(row.side).toLowerCase() === "buy") {
          bids.push([price, quantity]);
        } else {
          asks.push([price, quantity]);
        }
      });

      // Get sequence
      const seqResult = await pgPool.query(
        "SELECT last_seq FROM market_sequence WHERE market_id = $1",
        [marketId]
      );
      const sequence = seqResult.rows[0]?.last_seq || 0;

      client.ws.send(
        JSON.stringify({
          type: "snapshot",
          channel: "orderbook",
          symbol,
          data: {
            bids,
            asks,
            sequence: parseInt(sequence),
          },
          timestamp: Date.now(),
        })
      );
    } catch (error) {
      console.error("Error sending orderbook snapshot:", error);
    }
  }

  async function sendTradesSnapshot(client: Client, symbol: string) {
    try {
      const marketResult = await pgPool.query(
        "SELECT id FROM markets WHERE symbol = $1 AND active = true",
        [symbol]
      );

      if (marketResult.rows.length === 0) {
        return;
      }

      const marketId = marketResult.rows[0].id;

      const tradesResult = await pgPool.query(
        `
        SELECT 
          t.id,
          t.price,
          t.size as quantity,
          t.sequence,
          t.created_at,
          CASE 
            WHEN lower(taker_order.side) = 'buy' THEN 'buy'
            ELSE 'sell'
          END as side
        FROM trades t
        JOIN orders taker_order ON t.taker_order_id = taker_order.id
        WHERE t.market_id = $1
        ORDER BY t.sequence DESC
        LIMIT 50
      `,
        [marketId]
      );

      client.ws.send(
        JSON.stringify({
          type: "snapshot",
          channel: "trades",
          symbol,
          data: tradesResult.rows.map((row: any) => ({
            id: row.id,
            price: parseFloat(row.price),
            quantity: parseFloat(row.quantity),
            side: row.side,
            sequence: parseInt(row.sequence),
            timestamp: new Date(row.created_at).getTime(),
          })),
          timestamp: Date.now(),
        })
      );
    } catch (error) {
      console.error("Error sending trades snapshot:", error);
    }
  }

  async function sendTickerSnapshot(client: Client, symbol: string) {
    try {
      const marketResult = await pgPool.query(
        `
        SELECT 
          m.symbol,
          COALESCE(t.last_price, 0) as last_price,
          COALESCE(t.volume_24h, 0) as volume_24h,
          COALESCE(t.high_24h, 0) as high_24h,
          COALESCE(t.low_24h, 0) as low_24h,
          COALESCE(t.price_change_24h, 0) as price_change_24h
        FROM markets m
        LEFT JOIN LATERAL (
          SELECT 
            (array_agg(price ORDER BY created_at DESC))[1] as last_price,
            SUM(size) as volume_24h,
            MAX(price) as high_24h,
            MIN(price) as low_24h,
            (MAX(price) - MIN(price)) / NULLIF(MIN(price), 0) * 100 as price_change_24h
          FROM trades
          WHERE market_id = m.id
            AND created_at > NOW() - INTERVAL '24 hours'
        ) t ON true
        WHERE m.symbol = $1 AND m.active = true
      `,
        [symbol]
      );

      if (marketResult.rows.length === 0) {
        return;
      }

      const row = marketResult.rows[0];

      client.ws.send(
        JSON.stringify({
          type: "snapshot",
          channel: "ticker",
          symbol,
          data: {
            lastPrice: parseFloat(row.last_price) || 0,
            volume24h: parseFloat(row.volume_24h) || 0,
            high24h: parseFloat(row.high_24h) || 0,
            low24h: parseFloat(row.low_24h) || 0,
            priceChange24h: parseFloat(row.price_change_24h) || 0,
          },
          timestamp: Date.now(),
        })
      );
    } catch (error) {
      console.error("Error sending ticker snapshot:", error);
    }
  }

  async function pumpMarketEvents(
    subscription: AsyncIterable<{ subject: string }>,
    eventKind: "order" | "trade"
  ) {
    try {
      for await (const message of subscription) {
        const marketId = message.subject.split(".").pop();
        if (!marketId) {
          continue;
        }

        const symbol = await getMarketSymbol(marketId);
        if (!symbol) {
          continue;
        }

        await broadcastMarketSnapshots(symbol, eventKind);
      }
    } catch (error) {
      if (!wss.clients.size) {
        return;
      }
      console.error(`Error processing ${eventKind} market events:`, error);
    }
  }

  async function getMarketSymbol(marketId: string): Promise<string | null> {
    const cached = marketSymbolById.get(marketId);
    if (cached) {
      return cached;
    }

    const result = await pgPool.query(
      "SELECT symbol FROM markets WHERE id = $1 AND active = true",
      [marketId]
    );
    const symbol = result.rows[0]?.symbol;
    if (!symbol) {
      return null;
    }

    marketSymbolById.set(marketId, symbol);
    return symbol;
  }

  async function broadcastMarketSnapshots(symbol: string, eventKind: "order" | "trade") {
    const targets = Array.from(clients.values()).filter(
      (client) => client.ws.readyState === WebSocket.OPEN
    );
    if (targets.length === 0) {
      return;
    }

    await Promise.all(
      targets.map(async (client) => {
        const tasks: Promise<void>[] = [];

        if (client.subscriptions.has(`orderbook:${symbol}`)) {
          tasks.push(sendOrderbookSnapshot(client, symbol));
        }
        if (eventKind === "trade" && client.subscriptions.has(`trades:${symbol}`)) {
          tasks.push(sendTradesSnapshot(client, symbol));
        }
        if (eventKind === "trade" && client.subscriptions.has(`ticker:${symbol}`)) {
          tasks.push(sendTickerSnapshot(client, symbol));
        }

        await Promise.all(tasks);
      })
    );
  }

  // Cleanup on server shutdown
  wss.on("close", () => {
    clearInterval(heartbeatInterval);
    for (const subscription of natsSubscriptions) {
      subscription.unsubscribe();
    }
  });

  return wss;
}
