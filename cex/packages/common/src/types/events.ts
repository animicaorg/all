import { z } from "zod";

export const baseMessageSchema = z.object({
  event_id: z.string().uuid(),
  correlation_id: z.string().uuid().optional(),
  causation_id: z.string().uuid().optional(),
  created_at: z.string().datetime(),
  idempotency_key: z.string().optional()
});

export const orderSubmitSchema = baseMessageSchema.extend({
  type: z.literal("OrderSubmit"),
  user_id: z.string().uuid(),
  client_order_id: z.string().min(1),
  market: z.string().min(1),
  side: z.enum(["buy", "sell"]),
  order_type: z.enum(["LIMIT", "MARKET", "POST_ONLY", "IOC", "FOK"]).default("LIMIT"),
  price: z.number().nonnegative().optional(),
  quantity: z.number().positive()
});

export const orderAcceptedSchema = baseMessageSchema.extend({
  type: z.literal("OrderAccepted"),
  order_id: z.string().uuid(),
  user_id: z.string().uuid(),
  client_order_id: z.string().min(1),
  market: z.string().min(1),
  side: z.enum(["buy", "sell"]),
  price: z.number().positive(),
  quantity: z.number().positive()
});

export type OrderSubmit = z.infer<typeof orderSubmitSchema>;
export type OrderAccepted = z.infer<typeof orderAcceptedSchema>;

export const subjects = {
  orderSubmit: "cex.order.submit",
  orderCancel: "cex.order.cancel",
  withdrawRequest: "cex.withdraw.request",
  withdrawApprove: "cex.withdraw.approve",
  withdrawReject: "cex.withdraw.reject",
  walletAddressAssign: "cex.wallet.address.assign",
  orderAccepted: "cex.order.accepted",
  orderRejected: "cex.order.rejected",
  orderUpdated: "cex.order.updated",
  tradeExecuted: "cex.trade.executed",
  depositSeen: "cex.deposit.seen",
  depositConfirmed: "cex.deposit.confirmed",
  depositReorged: "cex.deposit.reorged",
  withdrawCreated: "cex.withdraw.created",
  withdrawBroadcast: "cex.withdraw.broadcast",
  withdrawConfirmed: "cex.withdraw.confirmed",
  withdrawFailed: "cex.withdraw.failed",
  ledgerEntryPosted: "cex.ledger.entry.posted",
  balanceUpdated: "cex.balance.updated"
} as const;
