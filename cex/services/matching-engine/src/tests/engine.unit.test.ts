/**
 * Unit tests for orderbook and matching engine
 */

import { describe, test, expect, beforeEach } from "vitest";
import { OrderBook } from "../engine/orderbook.js";
import { MatchingEngine } from "../engine/matching.js";
import { decimalToAtoms } from "../engine/deterministic.js";
import type { Order, MarketConfig } from "../engine/types.js";

const createTestMarketConfig = (): MarketConfig => ({
  id: "test-market",
  symbol: "BTC/USDT",
  baseAsset: "BTC",
  quoteAsset: "USDT",
  priceTick: 1n, // 1 atom = smallest unit
  sizeStep: 1n, // 1 atom = smallest unit
  minOrderSize: 1n,
  makerFeeBps: 10,
  takerFeeBps: 20,
  feeAsset: "USDT",
  active: true
});

const createTestOrder = (
  id: string,
  side: "BUY" | "SELL",
  priceAtoms: bigint,
  sizeAtoms: bigint,
  acceptedAt: Date = new Date("2024-01-01T00:00:00Z")
): any => ({
  id,
  userId: "user-1",
  clientOrderId: `client-${id}`,
  marketId: "test-market",
  side,
  orderType: "LIMIT",
  timeInForce: "GTC",
  priceAtoms,
  sizeAtoms,
  filledAtoms: 0n,
  remainingAtoms: sizeAtoms,
  postOnly: false,
  status: "ACCEPTED",
  acceptedAt
});

describe("OrderBook", () => {
  let book: OrderBook;

  beforeEach(() => {
    book = new OrderBook();
  });

  test("should add orders to the book", () => {
    const order = createTestOrder("1", "BUY", 10000n, 100n);
    book.add(order);
    expect(book.size()).toBe(1);
  });

  test("should get best bid (highest price)", () => {
    book.add(createTestOrder("1", "BUY", 10000n, 100n));
    book.add(createTestOrder("2", "BUY", 10100n, 100n));
    book.add(createTestOrder("3", "BUY", 9900n, 100n));

    const best = book.getBestBid();
    expect(best?.id).toBe("2");
    expect(best?.priceAtoms).toBe(10100n);
  });

  test("should get best ask (lowest price)", () => {
    book.add(createTestOrder("1", "SELL", 10000n, 100n));
    book.add(createTestOrder("2", "SELL", 10100n, 100n));
    book.add(createTestOrder("3", "SELL", 9900n, 100n));

    const best = book.getBestAsk();
    expect(best?.id).toBe("3");
    expect(best?.priceAtoms).toBe(9900n);
  });

  test("should maintain FIFO order at same price level", () => {
    const time1 = new Date("2024-01-01T00:00:00Z");
    const time2 = new Date("2024-01-01T00:00:01Z");
    const time3 = new Date("2024-01-01T00:00:02Z");

    book.add(createTestOrder("2", "BUY", 10000n, 100n, time2));
    book.add(createTestOrder("1", "BUY", 10000n, 100n, time1));
    book.add(createTestOrder("3", "BUY", 10000n, 100n, time3));

    const orders = book.getBidsAtPrice(10000n);
    expect(orders.map((o) => o.id)).toEqual(["1", "2", "3"]);
  });

  test("should maintain order by id when timestamps are equal", () => {
    const time = new Date("2024-01-01T00:00:00Z");

    book.add(createTestOrder("order-c", "BUY", 10000n, 100n, time));
    book.add(createTestOrder("order-a", "BUY", 10000n, 100n, time));
    book.add(createTestOrder("order-b", "BUY", 10000n, 100n, time));

    const orders = book.getBidsAtPrice(10000n);
    expect(orders.map((o) => o.id)).toEqual(["order-a", "order-b", "order-c"]);
  });

  test("should remove orders from the book", () => {
    const order = createTestOrder("1", "BUY", 10000n, 100n);
    book.add(order);
    expect(book.size()).toBe(1);

    const removed = book.remove("1");
    expect(removed?.id).toBe("1");
    expect(book.size()).toBe(0);
  });

  test("should detect crossing for post-only orders", () => {
    book.add(createTestOrder("1", "BUY", 10000n, 100n));
    book.add(createTestOrder("2", "SELL", 10100n, 100n));

    // Sell at 10000 or below crosses with bid at 10000
    expect(book.wouldCross("SELL", 10000n)).toBe(true);
    expect(book.wouldCross("SELL", 9900n)).toBe(true);
    expect(book.wouldCross("SELL", 10001n)).toBe(false);
    expect(book.wouldCross("SELL", 10100n)).toBe(false);

    // Buy at 10100 or above crosses with ask at 10100
    expect(book.wouldCross("BUY", 10100n)).toBe(true);
    expect(book.wouldCross("BUY", 10200n)).toBe(true);
    expect(book.wouldCross("BUY", 10099n)).toBe(false);
    expect(book.wouldCross("BUY", 10000n)).toBe(false);
  });

  test("should get snapshot of the book", () => {
    book.add(createTestOrder("1", "BUY", 10000n, 100n));
    book.add(createTestOrder("2", "BUY", 9900n, 50n));
    book.add(createTestOrder("3", "SELL", 10100n, 75n));
    book.add(createTestOrder("4", "SELL", 10200n, 25n));

    const snapshot = book.snapshot();
    expect(snapshot.bids.length).toBe(2);
    expect(snapshot.asks.length).toBe(2);
    expect(snapshot.bids[0].priceAtoms).toBe(10000n);
    expect(snapshot.asks[0].priceAtoms).toBe(10100n);
  });
});

describe("MatchingEngine - Basic Matching", () => {
  let engine: MatchingEngine;
  let marketConfig: MarketConfig;

  beforeEach(() => {
    marketConfig = createTestMarketConfig();
    engine = new MatchingEngine(marketConfig);
  });

  test("should match one bid with one ask exactly", () => {
    const maker = createTestOrder("maker", "SELL", 10000n, 100n);
    engine.addOrder(maker);

    const taker = createTestOrder("taker", "BUY", 10000n, 100n);
    const result = engine.match(taker);

    expect(result.fills.length).toBe(1);
    expect(result.fills[0].sizeAtoms).toBe(100n);
    expect(result.fills[0].priceAtoms).toBe(10000n);
    expect(result.trades.length).toBe(1);
    expect(result.takerOrder.status).toBe("FILLED");
    expect(result.takerOrder.filledAtoms).toBe(100n);
    expect(result.takerOrder.remainingAtoms).toBe(0n);
  });

  test("should partially fill taker when maker is smaller", () => {
    const maker = createTestOrder("maker", "SELL", 10000n, 50n);
    engine.addOrder(maker);

    const taker = createTestOrder("taker", "BUY", 10000n, 100n);
    const result = engine.match(taker);

    expect(result.fills.length).toBe(1);
    expect(result.fills[0].sizeAtoms).toBe(50n);
    expect(result.takerOrder.status).toBe("PARTIAL_FILL");
    expect(result.takerOrder.filledAtoms).toBe(50n);
    expect(result.takerOrder.remainingAtoms).toBe(50n);
  });

  test("should partially fill maker when taker is smaller", () => {
    const maker = createTestOrder("maker", "SELL", 10000n, 100n);
    engine.addOrder(maker);

    const taker = createTestOrder("taker", "BUY", 10000n, 50n);
    const result = engine.match(taker);

    expect(result.fills.length).toBe(1);
    expect(result.fills[0].sizeAtoms).toBe(50n);
    expect(result.takerOrder.status).toBe("FILLED");
    expect(result.takerOrder.filledAtoms).toBe(50n);
    expect(result.takerOrder.remainingAtoms).toBe(0n);

    const makerUpdate = result.makerUpdates.get("maker");
    expect(makerUpdate?.status).toBe("PARTIAL_FILL");
    expect(makerUpdate?.filledAtoms).toBe(50n);
    expect(makerUpdate?.remainingAtoms).toBe(50n);
  });

  test("should match across multiple price levels", () => {
    engine.addOrder(createTestOrder("ask1", "SELL", 10000n, 30n));
    engine.addOrder(createTestOrder("ask2", "SELL", 10100n, 40n));
    engine.addOrder(createTestOrder("ask3", "SELL", 10200n, 50n));

    const taker = createTestOrder("taker", "BUY", 10200n, 100n);
    const result = engine.match(taker);

    expect(result.fills.length).toBe(3);
    expect(result.trades.length).toBe(3);
    expect(result.takerOrder.status).toBe("FILLED"); // 30 + 40 + 30 = 100
    expect(result.takerOrder.filledAtoms).toBe(100n);
    expect(result.takerOrder.remainingAtoms).toBe(0n);
  });

  test("should not match when limit price prevents it", () => {
    engine.addOrder(createTestOrder("ask", "SELL", 10100n, 100n));

    const taker = createTestOrder("taker", "BUY", 10000n, 100n);
    const result = engine.match(taker);

    expect(result.fills.length).toBe(0);
    expect(result.trades.length).toBe(0);
    expect(result.takerOrder.status).toBe("ACCEPTED");
    expect(result.takerOrder.filledAtoms).toBe(0n);
  });

  test("should match at maker price (not taker price)", () => {
    const maker = createTestOrder("maker", "SELL", 10000n, 100n);
    engine.addOrder(maker);

    const taker = createTestOrder("taker", "BUY", 10100n, 100n);
    const result = engine.match(taker);

    expect(result.fills.length).toBe(1);
    expect(result.fills[0].priceAtoms).toBe(10000n); // Maker price wins
  });

  test("should calculate fees correctly", () => {
    // Use larger values to ensure fees are > 0
    const maker = createTestOrder("maker", "SELL", 100000000n, 100000000n); // 100M x 100M
    engine.addOrder(maker);

    const taker = createTestOrder("taker", "BUY", 100000000n, 100000000n);
    const result = engine.match(taker);

    const fill = result.fills[0];
    const trade = result.trades[0];

    // With larger values, fees should be > 0
    expect(fill.makerFeeAtoms).toBeGreaterThan(0n);
    expect(fill.takerFeeAtoms).toBeGreaterThan(0n);
    expect(fill.takerFeeAtoms).toBeGreaterThan(fill.makerFeeAtoms); // Taker fee is 2x maker
    expect(trade.makerFeeAtoms).toBe(fill.makerFeeAtoms);
    expect(trade.takerFeeAtoms).toBe(fill.takerFeeAtoms);
  });
});

describe("MatchingEngine - IOC and Post-Only", () => {
  let engine: MatchingEngine;
  let marketConfig: MarketConfig;

  beforeEach(() => {
    marketConfig = createTestMarketConfig();
    engine = new MatchingEngine(marketConfig);
  });

  test("should cancel remaining for IOC orders", () => {
    engine.addOrder(createTestOrder("ask", "SELL", 10000n, 50n));

    const taker = createTestOrder("taker", "BUY", 10000n, 100n);
    taker.timeInForce = "IOC";
    const result = engine.match(taker);

    expect(result.fills.length).toBe(1);
    expect(result.fills[0].sizeAtoms).toBe(50n);
    expect(result.takerOrder.filledAtoms).toBe(50n);
    expect(result.takerOrder.remainingAtoms).toBe(50n);
    // IOC: remaining should not rest on book
  });

  test("should reject post-only orders that would cross", () => {
    engine.addOrder(createTestOrder("ask", "SELL", 10000n, 100n));

    const taker = createTestOrder("taker", "BUY", 10000n, 100n);
    taker.postOnly = true;

    expect(() => engine.match(taker)).toThrow("Post-only order would cross");
  });

  test("should accept post-only orders that do not cross", () => {
    engine.addOrder(createTestOrder("ask", "SELL", 10100n, 100n));

    const taker = createTestOrder("taker", "BUY", 10000n, 100n);
    taker.postOnly = true;

    const result = engine.match(taker);
    expect(result.fills.length).toBe(0);
    expect(result.takerOrder.status).toBe("ACCEPTED");
  });
});

describe("MatchingEngine - Determinism", () => {
  let engine: MatchingEngine;
  let marketConfig: MarketConfig;

  beforeEach(() => {
    marketConfig = createTestMarketConfig();
    engine = new MatchingEngine(marketConfig);
  });

  test("should match orders in FIFO order at same price", () => {
    const time1 = new Date("2024-01-01T00:00:00Z");
    const time2 = new Date("2024-01-01T00:00:01Z");
    const time3 = new Date("2024-01-01T00:00:02Z");

    engine.addOrder(createTestOrder("ask2", "SELL", 10000n, 50n, time2));
    engine.addOrder(createTestOrder("ask1", "SELL", 10000n, 50n, time1));
    engine.addOrder(createTestOrder("ask3", "SELL", 10000n, 50n, time3));

    const taker = createTestOrder("taker", "BUY", 10000n, 100n);
    const result = engine.match(taker);

    expect(result.fills.length).toBe(2);
    expect(result.fills[0].makerOrderId).toBe("ask1"); // First by time
    expect(result.fills[1].makerOrderId).toBe("ask2"); // Second by time
  });

  test("should use order_id as tie-breaker for same timestamp", () => {
    const time = new Date("2024-01-01T00:00:00Z");

    engine.addOrder(createTestOrder("ask-c", "SELL", 10000n, 40n, time));
    engine.addOrder(createTestOrder("ask-a", "SELL", 10000n, 40n, time));
    engine.addOrder(createTestOrder("ask-b", "SELL", 10000n, 40n, time));

    const taker = createTestOrder("taker", "BUY", 10000n, 100n);
    const result = engine.match(taker);

    expect(result.fills.length).toBe(3);
    expect(result.fills[0].makerOrderId).toBe("ask-a"); // Lexicographic order
    expect(result.fills[1].makerOrderId).toBe("ask-b");
    expect(result.fills[2].makerOrderId).toBe("ask-c");
  });

  test("should maintain sequence numbers", () => {
    engine.setSequence(100n);

    const maker = createTestOrder("maker", "SELL", 10000n, 100n);
    engine.addOrder(maker);

    const taker = createTestOrder("taker", "BUY", 10000n, 100n);
    const result = engine.match(taker);

    expect(result.trades.length).toBe(1);
    expect(result.trades[0].sequence).toBe(101n);
    expect(engine.getCurrentSequence()).toBe(101n);
  });
});
