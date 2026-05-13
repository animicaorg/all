/**
 * @animica/agent-ui
 *
 * Tiny built-in HTTP bridge that serves a static dashboard and a small set
 * of read-only JSON endpoints for the local browser UI. The intent is to
 * give miners and developers a single place to glance at agent state
 * without standing up Vite/React.
 *
 * The bridge binds to 127.0.0.1 only. No remote endpoints, no auth.
 */

import { createServer, IncomingMessage, ServerResponse } from "node:http";
import { existsSync, readFileSync } from "node:fs";
import { extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  detectMinerIdentity,
  fetchBalance,
  formatANM,
  loadConfig,
  probeMinerLive,
  probeNode,
  resolveWalletIdentity,
  safeStringify,
} from "@animica/agent-core";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const PUBLIC_DIR = resolve(__dirname, "..", "public");

const CONTENT_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
};

function send(res: ServerResponse, status: number, body: string | Buffer, contentType = "text/plain; charset=utf-8"): void {
  res.statusCode = status;
  res.setHeader("content-type", contentType);
  res.setHeader("x-animica-agent", "ui");
  res.end(body);
}

async function jsonStatus(): Promise<unknown> {
  const { config, paths } = loadConfig();
  const node = await probeNode(config.rpcUrl).catch(() => null);
  const wallet = resolveWalletIdentity(config);
  const balance = wallet && node?.reachable
    ? await fetchBalance(config.rpcUrl, wallet.address).catch(() => null)
    : null;
  const identity = detectMinerIdentity(config);
  const live = await probeMinerLive(identity).catch(() => null);
  return {
    config: { ...config, providerBaseUrl: config.providerBaseUrl ? "[set]" : undefined },
    paths,
    node,
    wallet: wallet && balance ? { ...wallet, formattedANM: balance.formattedANM, raw: balance.raw } : wallet,
    miner: { identity, live },
  };
}

export interface BridgeOptions {
  port?: number;
  host?: string;
}

export function startBridge(opts: BridgeOptions = {}): { url: string; close(): void } {
  const host = opts.host ?? "127.0.0.1";
  const port = opts.port ?? 4720;

  const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
    const url = new URL(req.url ?? "/", `http://${host}:${port}`);
    if (url.pathname === "/api/status") {
      try {
        const body = safeStringify(await jsonStatus(), { indent: 2 });
        return send(res, 200, body, "application/json; charset=utf-8");
      } catch (err) {
        return send(res, 500, safeStringify({ error: (err as Error).message }), "application/json");
      }
    }
    if (url.pathname === "/api/health") {
      return send(res, 200, '{"ok":true}', "application/json");
    }
    // Static asset routing.
    const safe = url.pathname === "/" ? "/index.html" : url.pathname;
    if (safe.includes("..")) return send(res, 400, "bad path");
    const filePath = join(PUBLIC_DIR, safe);
    if (!existsSync(filePath)) return send(res, 404, "not found");
    try {
      const buf = readFileSync(filePath);
      return send(res, 200, buf, CONTENT_TYPES[extname(filePath)] ?? "application/octet-stream");
    } catch {
      return send(res, 500, "read error");
    }
  });
  server.listen(port, host);
  const url = `http://${host}:${port}/`;
  return { url, close: () => server.close() };
}
