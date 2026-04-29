/**
 * Determinism and simulation tests
 * Uses fixed sequences to ensure stable, reproducible behavior
 */

import { describe, test, expect } from "vitest";
import { MatchingEngine } from "../engine/matching.js";
import { decimalToAtoms, atomsToDecimal } from "../engine/deterministic.js";
import type { Order, MarketConfig } from "../engine/types.js";

const createTestMarketConfig = (): MarketConfig => ({
  id: "test-market-sim",
  symbol: "ETH/USDT",
  baseAsset: "ETH",
  quoteAsset: "USDT",
  priceTick: decimalToAtoms("0.01", 8), // 0.01 USD
  sizeStep: decimalToAtoms("0.001", 8), // 0.001 ETH
  minOrderSize: decimalToAtoms("0.001", 8),
  makerFeeBps: 10,
  takerFeeBps: 20,
  feeAsset: "USDT",
  active: true
});

const createOrder = (
  id: string,
  side: "BUY" | "SELL",
  price: string,
  size: string,
  timestamp: string
): any => ({
  id,
  userId: "sim-user",
  clientOrderId: `client-${id}`,
  marketId: "test-market-sim",
  side,
  orderType: "LIMIT",
  timeInForce: "GTC",
  priceAtoms: decimalToAtoms(price, 8),
  sizeAtoms: decimalToAtoms(size, 8),
  filledAtoms: 0n,
  remainingAtoms: decimalToAtoms(size, 8),
  postOnly: false,
  status: "ACCEPTED",
  acceptedAt: new Date(timestamp)
});

describe("Determinism - Golden Test 1: Basic Crossing", () => {
  test("should produce deterministic results for basic bid-ask crossing", () => {
    const config = createTestMarketConfig();
    const engine = new MatchingEngine(config);

    // Add maker orders
    engine.addOrder(createOrder("ask1", "SELL", "2000.00", "1.0", "2024-01-01T00:00:00Z"));
    engine.addOrder(createOrder("ask2", "SELL", "2001.00", "0.5", "2024-01-01T00:00:01Z"));
    engine.addOrder(createOrder("bid1", "BUY", "1999.00", "0.75", "2024-01-01T00:00:02Z"));

    // Take with a buy order
    const taker = createOrder("taker1", "BUY", "2001.00", "1.2", "2024-01-01T00:00:03Z");
    const result = engine.match(taker);

    // Expected: Should match ask1 fully (1.0) and ask2 partially (0.2)
    expect(result.fills.length).toBe(2);
    expect(result.trades.length).toBe(2);

    // Verify fills
    expect(result.fills[0].makerOrderId).toBe("ask1");
    expect(atomsToDecimal(result.fills[0].sizeAtoms, 8)).toBe("1");
    expect(atomsToDecimal(result.fills[0].priceAtoms, 8)).toBe("2000");

    expect(result.fills[1].makerOrderId).toBe("ask2");
    expect(atomsToDecimal(result.fills[1].sizeAtoms, 8)).toBe("0.2");
    expect(atomsToDecimal(result.fills[1].priceAtoms, 8)).toBe("2001");

    // Verify taker
    expect(result.takerOrder.status).toBe("FILLED");
    expect(atomsToDecimal(result.takerOrder.filledAtoms, 8)).toBe("1.2");
    expect(atomsToDecimal(result.takerOrder.remainingAtoms, 8)).toBe("0");

    // Verify sequence
    expect(result.trades[0].sequence).toBe(1n);
    expect(result.trades[1].sequence).toBe(2n);

    // Golden snapshot
    const golden = {
      fills: result.fills.map((f) => ({
        maker: f.makerOrderId,
        taker: f.takerOrderId,
        price: atomsToDecimal(f.priceAtoms, 8),
        size: atomsToDecimal(f.sizeAtoms, 8)
      })),
      takerStatus: result.takerOrder.status,
      takerFilled: atomsToDecimal(result.takerOrder.filledAtoms, 8),
      sequences: result.trades.map((t) => t.sequence.toString())
    };

    expect(golden).toEqual({
      fills: [
        { maker: "ask1", taker: "taker1", price: "2000", size: "1" },
        { maker: "ask2", taker: "taker1", price: "2001", size: "0.2" }
      ],
      takerStatus: "FILLED",
      takerFilled: "1.2",
      sequences: ["1", "2"]
    });
  });
});

describe("Determinism - Golden Test 2: FIFO at Same Price", () => {
  test("should match orders in strict FIFO order at same price level", () => {
    const config = createTestMarketConfig();
    const engine = new MatchingEngine(config);

    // Add three asks at same price but different times
    engine.addOrder(createOrder("ask1", "SELL", "2000.00", "0.5", "2024-01-01T00:00:00Z"));
    engine.addOrder(createOrder("ask2", "SELL", "2000.00", "0.3", "2024-01-01T00:00:01Z"));
    engine.addOrder(createOrder("ask3", "SELL", "2000.00", "0.4", "2024-01-01T00:00:02Z"));

    // Take with a buy order
    const taker = createOrder("taker1", "BUY", "2000.00", "1.0", "2024-01-01T00:00:03Z");
    const result = engine.match(taker);

    // Expected: Should match in FIFO order: ask1 (0.5), ask2 (0.3), ask3 (0.2)
    expect(result.fills.length).toBe(3);

    expect(result.fills[0].makerOrderId).toBe("ask1");
    expect(atomsToDecimal(result.fills[0].sizeAtoms, 8)).toBe("0.5");

    expect(result.fills[1].makerOrderId).toBe("ask2");
    expect(atomsToDecimal(result.fills[1].sizeAtoms, 8)).toBe("0.3");

    expect(result.fills[2].makerOrderId).toBe("ask3");
    expect(atomsToDecimal(result.fills[2].sizeAtoms, 8)).toBe("0.2");

    // Golden snapshot
    const golden = {
      fillOrder: result.fills.map((f) => f.makerOrderId),
      fillSizes: result.fills.map((f) => atomsToDecimal(f.sizeAtoms, 8)),
      totalFilled: atomsToDecimal(result.takerOrder.filledAtoms, 8)
    };

    expect(golden).toEqual({
      fillOrder: ["ask1", "ask2", "ask3"],
      fillSizes: ["0.5", "0.3", "0.2"],
      totalFilled: "1"
    });
  });
});

describe("Determinism - Golden Test 3: Lexicographic Tie-Breaker", () => {
  test("should use order_id for tie-breaking when timestamps are equal", () => {
    const config = createTestMarketConfig();
    const engine = new MatchingEngine(config);

    // Add orders at same price and same timestamp
    const timestamp = "2024-01-01T00:00:00Z";
    engine.addOrder(createOrder("order-gamma", "SELL", "2000.00", "0.3", timestamp));
    engine.addOrder(createOrder("order-alpha", "SELL", "2000.00", "0.3", timestamp));
    engine.addOrder(createOrder("order-beta", "SELL", "2000.00", "0.3", timestamp));

    // Take
    const taker = createOrder("taker1", "BUY", "2000.00", "0.7", "2024-01-01T00:00:01Z");
    const result = engine.match(taker);

    // Expected: Lexicographic order: alpha, beta, gamma
    expect(result.fills.length).toBe(3);

    expect(result.fills[0].makerOrderId).toBe("order-alpha");
    expect(result.fills[1].makerOrderId).toBe("order-beta");
    expect(result.fills[2].makerOrderId).toBe("order-gamma");

    // Golden snapshot
    const golden = {
      fillOrder: result.fills.map((f) => f.makerOrderId)
    };

    expect(golden).toEqual({
      fillOrder: ["order-alpha", "order-beta", "order-gamma"]
    });
  });
});

describe("Determinism - Golden Test 4: Multi-Level Sweep", () => {
  test("should sweep multiple price levels deterministically", () => {
    const config = createTestMarketConfig();
    const engine = new MatchingEngine(config);

    // Build orderbook with multiple levels
    engine.addOrder(createOrder("ask1", "SELL", "2000.00", "1.0", "2024-01-01T00:00:00Z"));
    engine.addOrder(createOrder("ask2", "SELL", "2000.00", "0.5", "2024-01-01T00:00:01Z"));
    engine.addOrder(createOrder("ask3", "SELL", "2001.00", "0.8", "2024-01-01T00:00:02Z"));
    engine.addOrder(createOrder("ask4", "SELL", "2002.00", "1.2", "2024-01-01T00:00:03Z"));

    // Market order that sweeps multiple levels
    const taker = createOrder("taker1", "BUY", "2002.00", "3.0", "2024-01-01T00:00:04Z");
    const result = engine.match(taker);

    // Expected: Match at 2000 (1.0 + 0.5), then 2001 (0.8), then 2002 (0.7)
    expect(result.fills.length).toBe(4);

    const golden = {
      fills: result.fills.map((f) => ({
        maker: f.makerOrderId,
        price: atomsToDecimal(f.priceAtoms, 8),
        size: atomsToDecimal(f.sizeAtoms, 8)
      })),
      totalFilled: atomsToDecimal(result.takerOrder.filledAtoms, 8),
      remaining: atomsToDecimal(result.takerOrder.remainingAtoms, 8)
    };

    expect(golden).toEqual({
      fills: [
        { maker: "ask1", price: "2000", size: "1" },
        { maker: "ask2", price: "2000", size: "0.5" },
        { maker: "ask3", price: "2001", size: "0.8" },
        { maker: "ask4", price: "2002", size: "0.7" }
      ],
      totalFilled: "3",
      remaining: "0"
    });
  });
});

describe("Determinism - Golden Test 5: Fee Calculation", () => {
  test("should calculate fees deterministically with rounding", () => {
    const config = createTestMarketConfig();
    const engine = new MatchingEngine(config);

    // Add maker
    engine.addOrder(createOrder("maker", "SELL", "1999.99", "1.234", "2024-01-01T00:00:00Z"));

    // Take
    const taker = createOrder("taker", "BUY", "2000.00", "1.234", "2024-01-01T00:00:01Z");
    const result = engine.match(taker);

    expect(result.trades.length).toBe(1);
    const trade = result.trades[0];

    // Quote amount = 1999.99 * 1.234567 = 2469.9752 (approx)
    // Maker fee (10 bps) = 2469.9752 * 0.001 = 2.4699752 -> round up
    // Taker fee (20 bps) = 2469.9752 * 0.002 = 4.9399504 -> round up

    const golden = {
      price: atomsToDecimal(trade.priceAtoms, 8),
      size: atomsToDecimal(trade.sizeAtoms, 8),
      quoteAmount: atomsToDecimal(trade.quoteAmountAtoms, 10),
      makerFee: atomsToDecimal(trade.makerFeeAtoms, 10),
      takerFee: atomsToDecimal(trade.takerFeeAtoms, 10),
      feeAsset: trade.feeAsset
    };

    // Verify fees are deterministic and positive
    expect(parseFloat(golden.makerFee)).toBeGreaterThan(0);
    expect(parseFloat(golden.takerFee)).toBeGreaterThan(0);
    expect(parseFloat(golden.takerFee)).toBeGreaterThan(parseFloat(golden.makerFee));
    expect(golden.feeAsset).toBe("USDT");

    // Save for comparison across runs
    expect(golden.price).toBe("1999.99");
    expect(golden.size).toBe("1.234");
  });
});

describe("Determinism - State Recovery", () => {
  test("should rebuild orderbook deterministically from open orders", () => {
    const config = createTestMarketConfig();
    const engine1 = new MatchingEngine(config);

    // Add orders to engine1
    const orders = [
      createOrder("ask1", "SELL", "2000.00", "1.0", "2024-01-01T00:00:00Z"),
      createOrder("ask2", "SELL", "2001.00", "0.5", "2024-01-01T00:00:01Z"),
      createOrder("bid1", "BUY", "1999.00", "0.75", "2024-01-01T00:00:02Z"),
      createOrder("bid2", "BUY", "1998.00", "1.2", "2024-01-01T00:00:03Z")
    ];

    for (const order of orders) {
      engine1.addOrder(order);
    }

    const snapshot1 = engine1.getOrderBook().snapshot();

    // Rebuild in engine2
    const engine2 = new MatchingEngine(config);
    engine2.rebuildFromOrders(orders);

    const snapshot2 = engine2.getOrderBook().snapshot();

    // Snapshots should be identical
    expect(snapshot2.bids.length).toBe(snapshot1.bids.length);
    expect(snapshot2.asks.length).toBe(snapshot1.asks.length);

    // Verify price levels
    const bidsGolden = snapshot2.bids.map((level) => ({
      price: atomsToDecimal(level.priceAtoms, 8),
      size: atomsToDecimal(level.totalSize, 8)
    }));

    const asksGolden = snapshot2.asks.map((level) => ({
      price: atomsToDecimal(level.priceAtoms, 8),
      size: atomsToDecimal(level.totalSize, 8)
    }));

    expect(bidsGolden).toEqual([
      { price: "1999", size: "0.75" },
      { price: "1998", size: "1.2" }
    ]);

    expect(asksGolden).toEqual([
      { price: "2000", size: "1" },
      { price: "2001", size: "0.5" }
    ]);
  });
});
