import { Router } from "express";
import type { Pool } from "pg";

const GOOGLE_FINANCE_QUOTES: Record<string, string> = {
  BNB: "BNB-USD",
  BTC: "BTC-USD",
  LTC: "LTC-USD",
  DOGE: "DOGE-USD",
  USDT: "USDT-USD",
  ZEC: "ZEC-USD",
};

type UsdQuote = {
  asset: string;
  usd: number;
  source: "google-finance" | "derived";
  sourceUrl?: string;
  derivedFrom?: string;
  fetchedAt: string;
};

type CacheEntry = {
  expiresAt: number;
  quotes: Map<string, UsdQuote>;
  failedAssets: string[];
};

let cache: CacheEntry | null = null;

function getGoogleFinanceUrl(asset: string): string | null {
  const quote = GOOGLE_FINANCE_QUOTES[asset.toUpperCase()];
  return quote ? getGoogleFinanceQuoteUrl(quote) : null;
}

function getGoogleFinanceQuoteUrl(quote: string): string {
  return `https://www.google.com/finance/quote/${quote}?hl=en`;
}

function parseGoogleFinancePrice(html: string, asset: string): number | null {
  const direct = html.match(/data-last-price="([0-9]+(?:\.[0-9]+)?)"/i);
  if (direct?.[1]) return Number(direct[1]);

  const pairMarker = `["${asset.toUpperCase()}","USD"`;
  let searchFrom = 0;
  while (searchFrom < html.length) {
    const pairIndex = html.indexOf(pairMarker, searchFrom);
    if (pairIndex < 0) break;
    const beforePair = html.slice(Math.max(0, pairIndex - 2_000), pairIndex);
    const priceBlocks = Array.from(
      beforePair.matchAll(/\[([0-9]+(?:\.[0-9]+)?),[-0-9.Ee]+,[-0-9.Ee]+,\d+,\d+,\d+\]/g)
    );
    const closest = priceBlocks.at(-1)?.[1];
    if (closest) return Number(closest);
    searchFrom = pairIndex + pairMarker.length;
  }

  const visible = html.match(/class="YMlKec fxKbKc">([^<]+)</i);
  if (visible?.[1]) {
    const normalized = visible[1].replace(/[$,\s]/g, "");
    const parsed = Number(normalized);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }

  const plainText = html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ");
  const suggestedQuote = plainText.match(
    new RegExp(`${asset.toUpperCase()}\\s+[^0-9]{0,120}\\(\\s*${asset.toUpperCase()}\\s*/\\s*${asset.toUpperCase()}\\s*\\)\\s*([0-9]+(?:\\.[0-9]+)?)`, "i")
  );
  if (suggestedQuote?.[1]) {
    const parsed = Number(suggestedQuote[1]);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }

  return null;
}

async function fetchGooglePrice(asset: string, sourceUrl: string): Promise<number | null> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 6_000);
  try {
    const response = await fetch(sourceUrl, {
      signal: controller.signal,
      headers: {
        "user-agent":
          "Mozilla/5.0 (compatible; AnimicaExchange/1.0; +https://animica.org)",
        accept: "text/html,application/xhtml+xml",
      },
    });
    if (!response.ok) return null;

    const html = await response.text();
    const usd = parseGoogleFinancePrice(html, asset);
    if (!usd || !Number.isFinite(usd) || usd <= 0) return null;

    return usd;
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchUsdtQuote(): Promise<UsdQuote | null> {
  const usdtIdrUrl = getGoogleFinanceQuoteUrl("USDT-IDR");
  const usdIdrUrl = getGoogleFinanceQuoteUrl("USD-IDR");
  const [usdtIdr, usdIdr] = await Promise.all([
    fetchGooglePrice("USDT", usdtIdrUrl).catch(() => null),
    fetchGooglePrice("USD", usdIdrUrl).catch(() => null),
  ]);

  if (!usdtIdr || !usdIdr || usdIdr <= 0) return null;

  return {
    asset: "USDT",
    usd: usdtIdr / usdIdr,
    source: "google-finance",
    sourceUrl: usdtIdrUrl,
    derivedFrom: "USDT-IDR divided by USD-IDR",
    fetchedAt: new Date().toISOString(),
  };
}

async function fetchGoogleQuote(asset: string): Promise<UsdQuote | null> {
  const normalizedAsset = asset.toUpperCase();
  if (normalizedAsset === "USDT") {
    const quote = await fetchUsdtQuote().catch(() => null);
    if (quote) return quote;
  }

  const sourceUrl = getGoogleFinanceUrl(normalizedAsset);
  if (!sourceUrl) return null;

  const usd = await fetchGooglePrice(normalizedAsset, sourceUrl);
  if (!usd) return null;

    return {
      asset: normalizedAsset,
      usd,
      source: "google-finance",
      sourceUrl,
      fetchedAt: new Date().toISOString(),
    };
}

async function deriveAnmQuote(pgPool: Pool, quotes: Map<string, UsdQuote>): Promise<UsdQuote | null> {
  const result = await pgPool.query(
    `
      SELECT
        m.symbol,
        m.base_asset,
        COALESCE(
          (SELECT t.price FROM trades t WHERE t.market_id = m.id ORDER BY t.created_at DESC LIMIT 1),
          NULL
        ) AS last_price
      FROM markets m
      WHERE m.active = true
        AND m.quote_asset = 'ANM'
      ORDER BY
        CASE m.base_asset
          WHEN 'BTC' THEN 1
          WHEN 'BNB' THEN 2
          WHEN 'LTC' THEN 3
          WHEN 'ZEC' THEN 4
          WHEN 'DOGE' THEN 5
          ELSE 6
        END
    `
  );

  for (const row of result.rows) {
    const baseAsset = String(row.base_asset).toUpperCase();
    const baseQuote = quotes.get(baseAsset);
    const marketPrice = Number(row.last_price);
    if (!baseQuote || !Number.isFinite(marketPrice) || marketPrice <= 0) continue;

    return {
      asset: "ANM",
      usd: baseQuote.usd / marketPrice,
      source: "derived",
      derivedFrom: `${row.symbol} last trade`,
      fetchedAt: new Date().toISOString(),
    };
  }

  return null;
}

async function loadQuotes(pgPool: Pool): Promise<CacheEntry> {
  const now = Date.now();
  if (cache && cache.expiresAt > now) return cache;

  const quotes = new Map<string, UsdQuote>();
  const failedAssets: string[] = [];

  await Promise.all(
    Object.keys(GOOGLE_FINANCE_QUOTES).map(async (asset) => {
      const quote = await fetchGoogleQuote(asset).catch(() => null);
      if (quote) {
        quotes.set(asset, quote);
      } else {
        failedAssets.push(asset);
      }
    })
  );

  const anmQuote = await deriveAnmQuote(pgPool, quotes).catch(() => null);
  if (anmQuote) quotes.set("ANM", anmQuote);

  cache = {
    expiresAt: now + 60_000,
    quotes,
    failedAssets,
  };
  return cache;
}

export function createPricesRouter(pgPool: Pool): Router {
  const router = Router();

  router.get("/prices/usd", async (req, res) => {
    try {
      const requestedAssets = String(req.query.assets ?? "BNB,BTC,LTC,ZEC,DOGE,USDT,ANM")
        .split(",")
        .map((asset) => asset.trim().toUpperCase())
        .filter(Boolean);
      const uniqueAssets = Array.from(new Set(requestedAssets));
      const entry = await loadQuotes(pgPool);

      res.json({
        quotes: uniqueAssets
          .map((asset) => entry.quotes.get(asset))
          .filter((quote): quote is UsdQuote => Boolean(quote)),
        failedAssets: uniqueAssets.filter(
          (asset) => !entry.quotes.has(asset) || entry.failedAssets.includes(asset)
        ),
        cacheTtlMs: Math.max(0, entry.expiresAt - Date.now()),
      });
    } catch (error) {
      console.error("Error fetching USD prices:", error);
      res.status(500).json({ error: "Failed to fetch USD prices" });
    }
  });

  return router;
}
