import { generateEventKey } from "../engine/deterministic.js";
import type { Order, Trade } from "../engine/types.js";
import type { OutboxRepo } from "../db/repositories/index.js";

export async function writeOrderEvent(
  repo: OutboxRepo,
  marketId: string,
  sequence: bigint,
  eventType: string,
  order: any
): Promise<void> {
  const key = generateEventKey(marketId, eventType, sequence, order.id);
  
  await repo.writeEvent({
    marketId,
    seq: sequence,
    type: "ORDER_EVENT",
    key,
    payload: {
      eventType,
      orderId: order.id,
      userId: order.userId,
      clientOrderId: order.clientOrderId,
      marketId: order.marketId,
      side: order.side,
      orderType: order.orderType,
      priceAtoms: order.priceAtoms.toString(),
      sizeAtoms: order.sizeAtoms.toString(),
      filledAtoms: order.filledAtoms.toString(),
      remainingAtoms: order.remainingAtoms.toString(),
      status: order.status,
      sequence: sequence.toString()
    }
  });
}

export async function writeTradeEvent(
  repo: OutboxRepo,
  trade: Trade
): Promise<void> {
  const key = generateEventKey(trade.marketId, "TRADE", trade.sequence);
  
  await repo.writeEvent({
    marketId: trade.marketId,
    seq: trade.sequence,
    type: "TRADE_EVENT",
    key,
    payload: {
      tradeId: trade.id,
      marketId: trade.marketId,
      makerOrderId: trade.makerOrderId,
      takerOrderId: trade.takerOrderId,
      priceAtoms: trade.priceAtoms.toString(),
      sizeAtoms: trade.sizeAtoms.toString(),
      quoteAmountAtoms: trade.quoteAmountAtoms.toString(),
      makerFeeAtoms: trade.makerFeeAtoms.toString(),
      takerFeeAtoms: trade.takerFeeAtoms.toString(),
      feeAsset: trade.feeAsset,
      feeBpsMaker: trade.feeBpsMaker,
      feeBpsTaker: trade.feeBpsTaker,
      sequence: trade.sequence.toString(),
      createdAt: trade.createdAt.toISOString()
    }
  });
}
