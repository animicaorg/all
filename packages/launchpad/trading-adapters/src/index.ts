export * from "./types";
export * from "./mock";
export * from "./native";
export * from "./bonding";

import { MockTradingAdapter } from "./mock";
import { AnimicaNativeTradingAdapter } from "./native";
import { AnimicaBondingCurveAdapter } from "./bonding";
import type { TradingAdapter } from "./types";

export interface TradingAdapterEnv {
  ENABLE_REAL_TRADING?: string;
  ENABLE_BONDING_CURVE?: string;
}

export function createTradingAdapterFromEnv(env: TradingAdapterEnv): TradingAdapter {
  const truthy = (v?: string) => v != null && /^(1|true|yes)$/i.test(v);
  if (truthy(env.ENABLE_REAL_TRADING)) {
    return new AnimicaNativeTradingAdapter();
  }
  if (truthy(env.ENABLE_BONDING_CURVE)) {
    return new AnimicaBondingCurveAdapter();
  }
  return new MockTradingAdapter();
}
