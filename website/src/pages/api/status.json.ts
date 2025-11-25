import type { APIRoute } from "astro";
import { env } from "../../env";

/**
 * /api/status.json
 * Proxies the public RPC to gather a small status payload:
 *  - head { height, hash }
 *  - estimated TPS (best-effort)
 *  - rpc latency
 *
 * This runs server-side to avoid CORS/credentials issues in the browser.
 */

type JsonRpcResult<T> = { jsonrpc: "2.0"; id: number | string | null; result?: T; error?: { code: number; message: string; data?: unknown } };

const RPC_URL = env.PUBLIC_RPC_URL;
const DEFAULT_TARGET_BLOCK_TIME_SEC = 12;

async function rpcCall<T = any>(method: string, params: unknown[] = [], signal?: AbortSignal): Promise<T> {
  const body = { jsonrpc: "2.0", id: 1, method, params };
  const res = await fetch(RPC_URL.replace(/\/+$/, "") + "/rpc", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    throw new Error(`RPC HTTP ${res.status} ${res.statusText}`);
  }
  const data = (await res.json()) as JsonRpcResult<T>;
  if (data.error) {
    throw new Error(`RPC ${method} error ${data.error.code}: ${data.error.message}`);
  }
  return data.result as T;
}

type Head = { height: number; hash: string; [k: string]: unknown };
type ChainParams = { targetBlockSeconds?: number; target_block_time_sec?: number; [k: string]: unknown };

/** Try to count txs in a block across a few possible shapes. */
function countTxs(block: any): number {
  if (!block) return 0;
  if (Array.isArray(block.txs)) return block.txs.length;
  if (Array.isArray(block.transactions)) return block.transactions.length;
  if (Array.isArray(block.body?.txs)) return block.body.txs.length;
  if (typeof block.txCount === "number") return block.txCount;
  return 0;
}

/** Try to extract a UNIX epoch (seconds) timestamp from a block. */
function blockTimestamp(block: any): number | null {
  const ts =
    block?.header?.timestamp ??
    block?.header?.time ??
    block?.timestamp ??
    null;
  if (ts == null) return null;
  // Support seconds or ms
  const n = Number(ts);
  if (!Number.isFinite(n)) return null;
  return n > 1e12 ? Math.floor(n / 1000) : Math.floor(n);
}

/** Best-effort TPS estimate using recent blocks; falls back to target interval if timestamps are missing. */
async function estimateTps(headHeight: number, controller: AbortController): Promise<number | null> {
  const maxBlocks = 20;
  const heights: number[] = [];
  for (let h = headHeight; h > Math.max(0, headHeight - maxBlocks); h--) heights.push(h);

  // Try to read target block time from params
  let targetSec = DEFAULT_TARGET_BLOCK_TIME_SEC;
  try {
    const params = (await rpcCall<ChainParams>("chain.getParams", [], controller.signal)) || {};
    targetSec = Number(params.targetBlockSeconds ?? params.target_block_time_sec ?? DEFAULT_TARGET_BLOCK_TIME_SEC);
  } catch { /* ignore */ }

  let totalTxs = 0;
  let firstTs: number | null = null;
  let lastTs: number | null = null;
  let fetched = 0;

  for (const h of heights) {
    try {
      // Some implementations take (height, { includeTxs: true }), others just (height)
      let block: any;
      try {
        block = await rpcCall("chain.getBlockByNumber", [h, { includeTxs: true, includeReceipts: false }], controller.signal);
      } catch {
        block = await rpcCall("chain.getBlockByNumber", [h], controller.signal);
      }

      fetched++;
      totalTxs += countTxs(block);
      const ts = blockTimestamp(block);
      if (ts != null) {
        if (firstTs == null || ts < firstTs) firstTs = ts;
        if (lastTs == null || ts > lastTs) lastTs = ts;
      }
    } catch {
      // ignore individual block failures, continue
    }
  }

  if (fetched === 0) return null;

  // If we have timestamps for a span of blocks, use wall time
  if (firstTs != null && lastTs != null && lastTs > firstTs) {
    const spanSec = Math.max(1, lastTs - firstTs);
    return totalTxs / spanSec;
  }

  // Fallback: assume ~targetSec per block over the fetched window
  const denom = Math.max(1, fetched * targetSec);
  return totalTxs / denom;
}

export const GET: APIRoute = async () => {
  const started = Date.now();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8_000);

  try {
    // ping RPC with a cheap call
    const head = await rpcCall<Head>("chain.getHead", [], controller.signal);
    const latencyMs = Date.now() - started;

    // Estimate TPS (best-effort). Do not block the main path if it fails.
    let tps: number | null = null;
    try {
      tps = await estimateTps(Number(head.height), controller);
      if (tps != null && Number.isFinite(tps)) {
        // keep a sensible number of decimals
        tps = Math.round(tps * 100) / 100;
      } else {
        tps = null;
      }
    } catch {
      tps = null;
    }

    const payload = {
      ok: true as const,
      rpcUrl: RPC_URL,
      head: { height: Number(head.height) || 0, hash: String((head as any).hash || "") },
      tps,
      latencyMs,
      timeUTC: new Date().toISOString(),
    };

    const body = JSON.stringify(payload);
    return new Response(body, {
      status: 200,
      headers: {
        "content-type": "application/json; charset=utf-8",
        // cache a little to dampen bursts
        "cache-control": "public, max-age=10, s-maxage=10, stale-while-revalidate=20",
      },
    });
  } catch (err: any) {
    const body = JSON.stringify({
      ok: false as const,
      rpcUrl: RPC_URL,
      error: err?.message ?? "Unknown error",
      timeUTC: new Date().toISOString(),
    });
    return new Response(body, {
      status: 503,
      headers: { "content-type": "application/json; charset=utf-8" },
    });
  } finally {
    clearTimeout(timeout);
  }
};
