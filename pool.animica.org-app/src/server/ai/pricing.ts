// Pricing math for the broker. All money is a Decimal string (src/lib/money.ts).
//
//   customerCost = inTok/1000 * priceIn + outTok/1000 * priceOut
//   grossMargin  = customerCost − providerCost
//
// The customer is charged the catalog price regardless of which provider
// actually served the call; margin is the broker's take that later phases
// redistribute via NOWPayments.

import { add, div, mul, sub } from "@/lib/money";
import type { CatalogModel } from "./catalog";

export function customerCost(
  model: CatalogModel,
  inputTokens: number,
  outputTokens: number,
): string {
  const inCost = mul(div(inputTokens, 1000), model.priceInPer1kUsd);
  const outCost = mul(div(outputTokens, 1000), model.priceOutPer1kUsd);
  return add(inCost, outCost);
}

export function grossMargin(customerCostUsd: string, providerCostUsd: string): string {
  return sub(customerCostUsd, providerCostUsd);
}
