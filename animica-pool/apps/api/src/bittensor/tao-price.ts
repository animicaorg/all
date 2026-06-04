// TAO/USD oracle for converting recorded SN51 earnings. Env override wins
// (TAO_USD_PRICE), otherwise CoinGecko (same base URL as the payout oracle).
import { env } from "../config/env";

let cache: { at: number; usd: number } | null = null;

export async function taoUsd(): Promise<number> {
  const override = Number(env().TAO_USD_PRICE || 0);
  if (override > 0) return override;
  if (cache && Date.now() - cache.at < 60_000) return cache.usd;
  const res = await fetch(
    `${env().COINGECKO_BASE_URL.replace(/\/$/, "")}/simple/price?ids=bittensor&vs_currencies=usd`,
  );
  if (!res.ok) throw new Error(`CoinGecko ${res.status}`);
  const json = (await res.json()) as { bittensor?: { usd?: number } };
  const usd = Number(json?.bittensor?.usd ?? 0);
  if (!(usd > 0)) throw new Error("No TAO/USD price available");
  cache = { at: Date.now(), usd };
  return usd;
}
