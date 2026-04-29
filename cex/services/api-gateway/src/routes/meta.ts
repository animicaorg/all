import { Router } from "express";

const router= Router() as any;

/**
 * GET /meta - Capabilities endpoint
 * Returns supported endpoints, WebSocket channels, and rate limits
 */
router.get("/meta", (req: any, res: any) => {
  res.json({
    version: "0.1.0",
    capabilities: {
      rest: {
        markets: true,
        orderbook: true,
        trades: true,
        candles: true,
        orders: true,
        balances: true,
        auth: true,
      },
      ws: {
        enabled: true,
        channels: [
          "orderbook",
          "trades",
          "ticker",
          "user_orders",
          "user_trades",
        ],
        supportsSnapshot: true,
        supportsSequence: true,
      },
      orderTypes: ["LIMIT", "MARKET", "POST_ONLY", "IOC", "FOK"],
      features: {
        idempotency: true,
        bulkCancel: true,
        postOnly: true,
      },
    },
    rateLimits: {
      rest: {
        perSecond: 10,
        perMinute: 100,
      },
      ws: {
        subscriptionsPerConnection: 20,
        messagesPerSecond: 50,
      },
    },
  });
});

export default router;
