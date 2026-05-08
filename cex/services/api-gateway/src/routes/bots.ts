import { randomUUID } from "node:crypto";
import { Router } from "express";
import type { Pool, PoolClient } from "pg";
import type { NatsConnection } from "nats";
import { z } from "zod";
import { jsonCodec, subjects } from "@cex/common";
import {
  createApiKeyVerifier,
  createRequireAuth,
  requireApiKeyScope,
  type AuthenticatedRequest,
} from "./authenticated.js";

type BotMode = "DCA" | "GRID" | "MAKER";

type BotRow = {
  id: string;
  user_id: string;
  mode: BotMode;
  market: string;
  status: "RUNNING" | "STOPPED" | "ERROR";
  config: any;
};

type BotCommand = {
  event_id: string;
  correlation_id: string;
  causation_id: string;
  created_at: string;
  idempotency_key: string;
  type: "OrderSubmit";
  user_id: string;
  client_order_id: string;
  market: string;
  side: "buy" | "sell";
  order_type: "LIMIT" | "MARKET" | "POST_ONLY";
  price: number;
  quantity: number;
};

const baseBotSchema = z.object({
  mode: z.enum(["DCA", "GRID", "MAKER"]),
  market: z.string().min(3).max(32),
  side: z.enum(["buy", "sell"]).optional(),
  quantity: z.number().positive(),
  intervalSeconds: z.number().int().min(30).max(24 * 60 * 60).optional(),
  spacingPct: z.number().positive().max(50).optional(),
  spreadPct: z.number().positive().max(50).optional(),
  levels: z.number().int().min(1).max(5).optional(),
});

const stopBotSchema = z.object({
  id: z.string().uuid(),
});

function normalizeBotInput(body: unknown) {
  const parsed = baseBotSchema.parse(body);
  const intervalSeconds =
    parsed.intervalSeconds ?? (parsed.mode === "DCA" ? 60 * 60 : parsed.mode === "GRID" ? 5 * 60 : 2 * 60);

  return {
    mode: parsed.mode,
    market: parsed.market.toUpperCase(),
    config: {
      side: parsed.side ?? "buy",
      quantity: parsed.quantity,
      intervalSeconds,
      spacingPct: parsed.spacingPct ?? 1,
      spreadPct: parsed.spreadPct ?? 0.6,
      levels: parsed.levels ?? 1,
    },
  };
}

function mapBot(row: any) {
  return {
    id: row.id,
    mode: row.mode,
    market: row.market,
    status: row.status,
    config: row.config ?? {},
    lastError: row.last_error ?? null,
    lastRunAt: row.last_run_at ? new Date(row.last_run_at).toISOString() : null,
    nextRunAt: row.next_run_at ? new Date(row.next_run_at).toISOString() : null,
    createdAt: row.created_at ? new Date(row.created_at).toISOString() : null,
    updatedAt: row.updated_at ? new Date(row.updated_at).toISOString() : null,
  };
}

function toNumber(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function roundToStep(value: number, step: number, direction: "down" | "up"): number {
  if (!Number.isFinite(step) || step <= 0) return value;
  const scaled = value / step;
  return (direction === "down" ? Math.floor(scaled) : Math.ceil(scaled)) * step;
}

function decimalPlaces(step: number): number {
  if (!Number.isFinite(step) || step <= 0) return 8;
  const fixed = step.toString();
  if (fixed.includes("e-")) return Number(fixed.split("e-")[1]);
  return fixed.includes(".") ? fixed.split(".")[1].length : 0;
}

function cleanNumber(value: number, decimals = 8): number {
  return Number(value.toFixed(decimals));
}

async function getMarketSnapshot(client: PoolClient, market: string) {
  const result = await client.query(
    `
      SELECT
        m.id::text,
        m.symbol,
        m.price_tick,
        m.size_step,
        m.min_order_size,
        COALESCE(
          (SELECT t.price FROM trades t WHERE t.market_id = m.id ORDER BY t.created_at DESC LIMIT 1),
          0
        ) AS last_price,
        COALESCE(
          (SELECT MAX(o.price) FROM orders o WHERE o.market_id = m.id AND lower(o.side) = 'buy' AND o.status IN ('ACCEPTED', 'PARTIAL_FILL') AND o.remaining_quantity > 0),
          0
        ) AS best_bid,
        COALESCE(
          (SELECT MIN(o.price) FROM orders o WHERE o.market_id = m.id AND lower(o.side) = 'sell' AND o.status IN ('ACCEPTED', 'PARTIAL_FILL') AND o.remaining_quantity > 0),
          0
        ) AS best_ask
      FROM markets m
      WHERE m.symbol = $1
        AND m.active = true
      LIMIT 1
    `,
    [market]
  );

  const row = result.rows[0];
  if (!row) throw new Error(`Market ${market} is not active`);

  const priceTick = toNumber(row.price_tick, 0.00000001);
  const sizeStep = toNumber(row.size_step, 0.00000001);
  const bestBid = toNumber(row.best_bid);
  const bestAsk = toNumber(row.best_ask);
  const lastPrice = toNumber(row.last_price);
  const referencePrice = bestBid > 0 && bestAsk > 0 ? (bestBid + bestAsk) / 2 : lastPrice;

  return {
    priceTick,
    sizeStep,
    minOrderSize: toNumber(row.min_order_size),
    bestBid,
    bestAsk,
    lastPrice,
    referencePrice,
  };
}

async function buildCancelCommands(client: PoolClient, bot: BotRow) {
  const result = await client.query(
    `
      SELECT id::text, market
      FROM orders
      WHERE user_id = $1::uuid
        AND client_order_id LIKE $2
        AND status IN ('ACCEPTED', 'PARTIAL_FILL')
    `,
    [bot.user_id, `bot-${bot.id.slice(0, 8)}-%`]
  );

  return result.rows.map((row) => ({
    event_id: randomUUID(),
    correlation_id: randomUUID(),
    causation_id: randomUUID(),
    created_at: new Date().toISOString(),
    type: "OrderCancel",
    user_id: bot.user_id,
    order_id: row.id,
    market: row.market,
  }));
}

function makeOrder(bot: BotRow, index: number, order: Omit<BotCommand, "event_id" | "correlation_id" | "causation_id" | "created_at" | "idempotency_key" | "type" | "user_id" | "client_order_id" | "market">): BotCommand {
  const eventId = randomUUID();
  const clientOrderId = `bot-${bot.id.slice(0, 8)}-${Date.now()}-${index}`;
  return {
    event_id: eventId,
    correlation_id: randomUUID(),
    causation_id: randomUUID(),
    created_at: new Date().toISOString(),
    idempotency_key: `bot-${bot.id}-${eventId}`,
    type: "OrderSubmit",
    user_id: bot.user_id,
    client_order_id: clientOrderId,
    market: bot.market,
    ...order,
  };
}

async function buildBotOrders(client: PoolClient, bot: BotRow): Promise<BotCommand[]> {
  const config = bot.config ?? {};
  const quantity = toNumber(config.quantity);
  if (!Number.isFinite(quantity) || quantity <= 0) throw new Error("Bot quantity must be greater than zero");

  const snapshot = await getMarketSnapshot(client, bot.market);
  const roundedQuantity = cleanNumber(roundToStep(quantity, snapshot.sizeStep, "down"));
  if (roundedQuantity <= 0 || (snapshot.minOrderSize > 0 && roundedQuantity < snapshot.minOrderSize)) {
    throw new Error(`Bot quantity is below the ${snapshot.minOrderSize} minimum`);
  }

  if (bot.mode === "DCA") {
    return [
      makeOrder(bot, 0, {
        side: config.side === "sell" ? "sell" : "buy",
        order_type: "MARKET",
        price: 0,
        quantity: roundedQuantity,
      }),
    ];
  }

  if (!Number.isFinite(snapshot.referencePrice) || snapshot.referencePrice <= 0) {
    throw new Error("No market price is available yet");
  }

  const priceDecimals = decimalPlaces(snapshot.priceTick);
  if (bot.mode === "GRID") {
    const levels = Math.max(1, Math.min(5, Math.trunc(toNumber(config.levels, 1))));
    const spacingPct = toNumber(config.spacingPct, 1) / 100;
    const orders: BotCommand[] = [];
    for (let level = 1; level <= levels; level += 1) {
      const downPrice = cleanNumber(
        roundToStep(snapshot.referencePrice * (1 - spacingPct * level), snapshot.priceTick, "down"),
        priceDecimals
      );
      const upPrice = cleanNumber(
        roundToStep(snapshot.referencePrice * (1 + spacingPct * level), snapshot.priceTick, "up"),
        priceDecimals
      );
      orders.push(
        makeOrder(bot, orders.length, {
          side: "buy",
          order_type: "LIMIT",
          price: downPrice,
          quantity: roundedQuantity,
        })
      );
      orders.push(
        makeOrder(bot, orders.length, {
          side: "sell",
          order_type: "LIMIT",
          price: upPrice,
          quantity: roundedQuantity,
        })
      );
    }
    return orders;
  }

  const halfSpread = toNumber(config.spreadPct, 0.6) / 200;
  const bid = cleanNumber(
    roundToStep(snapshot.referencePrice * (1 - halfSpread), snapshot.priceTick, "down"),
    priceDecimals
  );
  const ask = cleanNumber(
    roundToStep(snapshot.referencePrice * (1 + halfSpread), snapshot.priceTick, "up"),
    priceDecimals
  );

  return [
    makeOrder(bot, 0, {
      side: "buy",
      order_type: "POST_ONLY",
      price: bid,
      quantity: roundedQuantity,
    }),
    makeOrder(bot, 1, {
      side: "sell",
      order_type: "POST_ONLY",
      price: ask,
      quantity: roundedQuantity,
    }),
  ];
}

function nextRunSql(config: any): string {
  const intervalSeconds = Math.max(30, Math.min(24 * 60 * 60, Math.trunc(toNumber(config?.intervalSeconds, 300))));
  return `${intervalSeconds} seconds`;
}

async function runDueTradingBots(pgPool: Pool, nats: NatsConnection) {
  const client = await pgPool.connect();
  const publications: any[] = [];

  try {
    await client.query("BEGIN");
    const due = await client.query(
      `
        SELECT id::text, user_id::text, mode, market, status, config
        FROM trading_bots
        WHERE status = 'RUNNING'
          AND (next_run_at IS NULL OR next_run_at <= NOW())
        ORDER BY COALESCE(next_run_at, created_at) ASC
        LIMIT 20
        FOR UPDATE SKIP LOCKED
      `
    );

    for (const bot of due.rows as BotRow[]) {
      try {
        const cancelCommands = await buildCancelCommands(client, bot);
        const orderCommands = await buildBotOrders(client, bot);
        publications.push(...cancelCommands, ...orderCommands);
        await client.query(
          `
            UPDATE trading_bots
            SET
              last_order_ids = $2::jsonb,
              last_error = NULL,
              last_run_at = NOW(),
              next_run_at = NOW() + $3::interval,
              updated_at = NOW()
            WHERE id = $1::uuid
          `,
          [
            bot.id,
            JSON.stringify(orderCommands.map((command) => command.event_id)),
            nextRunSql(bot.config),
          ]
        );
      } catch (error: any) {
        await client.query(
          `
            UPDATE trading_bots
            SET
              last_error = $2,
              next_run_at = NOW() + INTERVAL '5 minutes',
              updated_at = NOW()
            WHERE id = $1::uuid
          `,
          [bot.id, error?.message ?? "Bot run failed"]
        );
      }
    }

    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    console.error("Trading bot runner failed:", error);
  } finally {
    client.release();
  }

  for (const message of publications) {
    const subject = message.type === "OrderCancel" ? subjects.orderCancel : subjects.orderSubmit;
    nats.publish(subject, jsonCodec.encode(message));
  }
}

export function startTradingBotRunner(pgPool: Pool, nats: NatsConnection): NodeJS.Timeout {
  const run = () => {
    runDueTradingBots(pgPool, nats).catch((error) => {
      console.error("Trading bot runner failed:", error);
    });
  };

  const interval = setInterval(run, 15_000);
  setTimeout(run, 2_000);
  return interval;
}

export function createBotsRouter(pgPool: Pool, nats: NatsConnection, authServiceUrl: string): Router {
  const router = Router();
  const requireAuth = createRequireAuth(authServiceUrl, {
    verifyApiKey: createApiKeyVerifier(pgPool),
  });
  const requireReadScope = requireApiKeyScope("read");
  const requireTradeScope = requireApiKeyScope("trade");

  router.get("/me/trading-bots", requireAuth, requireReadScope, async (req: AuthenticatedRequest, res) => {
    try {
      const result = await pgPool.query(
        `
          SELECT id::text, mode, market, status, config, last_error, last_run_at, next_run_at, created_at, updated_at
          FROM trading_bots
          WHERE user_id = $1::uuid
          ORDER BY created_at DESC
          LIMIT 50
        `,
        [req.userId]
      );
      res.json({ bots: result.rows.map(mapBot) });
    } catch (error) {
      console.error("Error listing trading bots:", error);
      res.status(500).json({ error: "Failed to list trading bots" });
    }
  });

  router.post("/me/trading-bots/start", requireAuth, requireTradeScope, async (req: AuthenticatedRequest, res) => {
    const client = await pgPool.connect();
    try {
      const bot = normalizeBotInput(req.body);
      await client.query("BEGIN");
      await client.query(
        `
          SELECT id
          FROM markets
          WHERE symbol = $1
            AND active = true
          LIMIT 1
        `,
        [bot.market]
      ).then((result) => {
        if (result.rows.length === 0) {
          throw new Error("Market is not active");
        }
      });

      await client.query(
        `
          UPDATE trading_bots
          SET status = 'STOPPED', updated_at = NOW()
          WHERE user_id = $1::uuid
            AND status = 'RUNNING'
        `,
        [req.userId]
      );

      const result = await client.query(
        `
          INSERT INTO trading_bots (user_id, mode, market, status, config, next_run_at)
          VALUES ($1::uuid, $2, $3, 'RUNNING', $4::jsonb, NOW())
          RETURNING id::text, mode, market, status, config, last_error, last_run_at, next_run_at, created_at, updated_at
        `,
        [req.userId, bot.mode, bot.market, JSON.stringify(bot.config)]
      );
      await client.query("COMMIT");
      res.status(201).json({ bot: mapBot(result.rows[0]) });
    } catch (error: any) {
      await client.query("ROLLBACK").catch(() => undefined);
      res.status(400).json({ error: error?.message ?? "Failed to start trading bot" });
    } finally {
      client.release();
    }
  });

  router.post("/me/trading-bots/:id/stop", requireAuth, requireTradeScope, async (req: AuthenticatedRequest, res) => {
    try {
      const params = stopBotSchema.parse(req.params);
      const result = await pgPool.query(
        `
          UPDATE trading_bots
          SET status = 'STOPPED', updated_at = NOW()
          WHERE id = $1::uuid
            AND user_id = $2::uuid
          RETURNING id::text, mode, market, status, config, last_error, last_run_at, next_run_at, created_at, updated_at
        `,
        [params.id, req.userId]
      );

      if (result.rows.length === 0) {
        return res.status(404).json({ error: "Trading bot not found" });
      }

      res.json({ bot: mapBot(result.rows[0]) });
    } catch (error) {
      console.error("Error stopping trading bot:", error);
      res.status(400).json({ error: "Failed to stop trading bot" });
    }
  });

  return router;
}
