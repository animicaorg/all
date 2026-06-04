// Price oracle (CoinGecko) to convert USD payout amounts into crypto amounts.
import { env } from "../config/env";

const COINGECKO_ID: Record<string, string> = {
  BTC: "bitcoin", SOL: "solana", XMR: "monero", USDT: "tether",
};

let cache: { at: number; prices: Record<string, number> } | null = null;

async function prices(): Promise<Record<string, number>> {
  if (cache && Date.now() - cache.at < 60_000) return cache.prices;
  const ids = Object.values(COINGECKO_ID).join(",");
  const res = await fetch(`${env().COINGECKO_BASE_URL.replace(/\/$/, "")}/simple/price?ids=${ids}&vs_currencies=usd`);
  if (!res.ok) throw new Error(`CoinGecko ${res.status}`);
  const json = (await res.json()) as Record<string, { usd: number }>;
  const out: Record<string, number> = {};
  for (const [asset, id] of Object.entries(COINGECKO_ID)) out[asset] = json[id]?.usd ?? 0;
  cache = { at: Date.now(), prices: out };
  return out;
}

/** USD → amount of `asset`. USDT is treated as $1. Throws if no price. */
export async function usdToAsset(asset: string, usd: number): Promise<number> {
  if (asset === "USDT") return +usd.toFixed(6);
  const p = await prices();
  const price = p[asset];
  if (!price || price <= 0) throw new Error(`No USD price for ${asset}`);
  return +(usd / price).toFixed(8);
}

export const PRICED_ASSETS = Object.keys(COINGECKO_ID);
