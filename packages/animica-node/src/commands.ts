/**
 * Operator command implementations.
 *
 * Each command is a thin glue layer: most actually delegate to the bundled
 * Python runtime via runBackend(...) so behavior is identical to the
 * upstream CLI. We only own:
 *   - the daemon pidfile / log files
 *   - the npm-side config (node.json)
 *   - human-friendly status output
 *   - the coding-agent integration hooks
 */

import { existsSync, readFileSync } from "node:fs";

import {
  detectMinerIdentity,
  formatANM,
  loadConfig as loadAgentConfig,
  probeMinerLive,
  probeNode,
  RpcClient,
  safeStringify,
} from "@animica/agent-core";

import { resolveBackend, runBackend } from "./backend.js";
import {
  DEFAULT_NODE_CONFIG,
  getNodeConfigPath,
  loadNodeConfig,
  writeNodeConfig,
  type NodeConfig,
} from "./config.js";
import { getDaemonPaths, isAlive, readPid, startDaemon, stopDaemon } from "./daemon.js";

export interface Result {
  exit: number;
  output: string;
}

function ok(output: string): Result {
  return { exit: 0, output };
}

function err(msg: string): Result {
  return { exit: 1, output: `error: ${msg}\n` };
}

export function init(): Result {
  const cfg = loadNodeConfig();
  const saved = writeNodeConfig({ ...DEFAULT_NODE_CONFIG, ...cfg });
  return ok(
    [
      `Wrote ${getNodeConfigPath()}`,
      safeStringify(saved, { indent: 2 }),
      "",
      "Next:",
      "  animica-node doctor",
      "  animica-node start",
    ].join("\n") + "\n",
  );
}

export async function status(): Promise<Result> {
  const cfg = loadNodeConfig();
  const paths = getDaemonPaths();
  const pid = readPid(paths);
  const alive = pid !== null && isAlive(pid);
  const rpcUrl = `http://127.0.0.1:${cfg.rpcPort}/rpc`;
  const probe = await probeNode(rpcUrl);
  const backend = resolveBackend();
  const data = {
    pid,
    alive,
    backend,
    rpcUrl,
    config: cfg,
    node: probe,
  };
  return ok(safeStringify(data, { indent: 2 }) + "\n");
}

export async function doctor(): Promise<Result> {
  const cfg = loadNodeConfig();
  const lines: string[] = [];
  let warnings = 0;
  lines.push("# animica-node doctor");
  const backend = resolveBackend();
  lines.push(`backend: ${backend.source} (kind=${backend.kind})`);
  // Reachability through bundled Python runtime.
  const dryRun = runBackend({ args: ["node", "doctor", "--help"], capture: true });
  lines.push(`backend doctor invocation: exit=${dryRun.status}`);
  if (dryRun.status !== 0) {
    warnings++;
    lines.push(`! backend doctor failed; stderr: ${dryRun.stderr.slice(0, 240)}`);
  }
  // Probe local node directly.
  const probe = await probeNode(`http://127.0.0.1:${cfg.rpcPort}/rpc`);
  lines.push(`rpc: reachable=${probe.reachable} chainId=${probe.chainId?.toString() ?? "—"}`);
  if (!probe.reachable) {
    warnings++;
    lines.push("! local node is not reachable. Start it with `animica-node start`.");
  }
  // Miner safety hint.
  const agentCfg = loadAgentConfig().config;
  const identity = detectMinerIdentity(agentCfg);
  const live = await probeMinerLive(identity).catch(() => null);
  lines.push(`miner: identity=${identity.source} metricsReachable=${live?.metricsReachable ?? false}`);
  if (live?.metricsReachable && cfg.resourceMode !== "miner-priority") {
    lines.push("# tip: an active miner is detected; set resourceMode=miner-priority to reduce contention.");
  }
  lines.push(`warnings=${warnings}`);
  return ok(lines.join("\n") + "\n");
}

export async function start(args: string[]): Promise<Result> {
  const cfg = loadNodeConfig();
  const foreground = args.includes("--foreground") || args.includes("-f");
  const r = startDaemon({ config: cfg, foreground });
  return ok(
    safeStringify(
      {
        action: "start",
        alreadyRunning: r.alreadyRunning,
        pid: r.pid,
        detached: r.detached,
        logFile: r.logFile,
        errFile: r.errFile,
        backendSource: r.backendSource,
      },
      { indent: 2 },
    ) + "\n",
  );
}

export function stop(): Result {
  const r = stopDaemon();
  return ok(safeStringify({ action: "stop", ...r }, { indent: 2 }) + "\n");
}

export async function restart(args: string[]): Promise<Result> {
  stopDaemon();
  return start(args);
}

export function logs(args: string[]): Result {
  const paths = getDaemonPaths();
  const follow = args.includes("-f") || args.includes("--follow");
  if (!existsSync(paths.logFile)) return err(`no log file at ${paths.logFile} (node not started yet?)`);
  const data = readFileSync(paths.logFile, "utf8");
  process.stdout.write(data);
  if (!follow) return ok("");
  // Simple file follow: re-read every 500ms.
  const fs = require("node:fs") as typeof import("node:fs");
  let pos = data.length;
  const interval = setInterval(() => {
    try {
      const stat = fs.statSync(paths.logFile);
      if (stat.size > pos) {
        const fd = fs.openSync(paths.logFile, "r");
        const buf = Buffer.alloc(stat.size - pos);
        fs.readSync(fd, buf, 0, buf.length, pos);
        fs.closeSync(fd);
        pos = stat.size;
        process.stdout.write(buf);
      }
    } catch {
      /* ignore transient errors */
    }
  }, 500);
  process.on("SIGINT", () => {
    clearInterval(interval);
    process.exit(0);
  });
  return ok("");
}

export async function rpcPassthrough(args: string[]): Promise<Result> {
  const [verb, method, ...rest] = args;
  if (verb !== "call" || !method) return err("usage: animica-node rpc call <method> [params…]");
  const cfg = loadNodeConfig();
  const client = new RpcClient({ url: `http://127.0.0.1:${cfg.rpcPort}/rpc` });
  const params = rest.map((t) => {
    try {
      return JSON.parse(t);
    } catch {
      return t;
    }
  });
  try {
    const r = await client.call({ method, params });
    return ok(safeStringify({ method, result: r }, { indent: 2 }) + "\n");
  } catch (e) {
    return err((e as Error).message);
  }
}

export async function peers(): Promise<Result> {
  const cfg = loadNodeConfig();
  const client = new RpcClient({ url: `http://127.0.0.1:${cfg.rpcPort}/rpc` });
  try {
    const p = await client.call<unknown[]>({ method: "animica_peers" }).catch(() => null);
    if (p) return ok(safeStringify({ peers: p }, { indent: 2 }) + "\n");
  } catch {
    /* fall through */
  }
  // Fallback to bundled CLI.
  const r = runBackend({ args: ["node", "peers"], capture: true });
  return { exit: r.status ?? 0, output: r.stdout || r.stderr || "" };
}

export async function syncStatus(): Promise<Result> {
  const cfg = loadNodeConfig();
  const probe = await probeNode(`http://127.0.0.1:${cfg.rpcPort}/rpc`);
  return ok(
    safeStringify(
      {
        rpcUrl: `http://127.0.0.1:${cfg.rpcPort}/rpc`,
        reachable: probe.reachable,
        chainId: probe.chainId?.toString(),
        blockNumber: probe.blockNumber?.toString(),
        syncing: probe.syncing ?? false,
      },
      { indent: 2 },
    ) + "\n",
  );
}

export function reset(args: string[]): Result {
  if (!args.includes("--yes") && !args.includes("--force")) {
    return err(
      "refusing to reset without --yes. This deletes the data dir defined in node.json. Pass --yes to confirm.",
    );
  }
  const cfg = loadNodeConfig();
  const fs = require("node:fs") as typeof import("node:fs");
  try {
    fs.rmSync(cfg.dataDir, { recursive: true, force: true });
  } catch (e) {
    return err((e as Error).message);
  }
  return ok(`reset: removed ${cfg.dataDir}\n`);
}

export function configShow(): Result {
  const cfg = loadNodeConfig();
  return ok(safeStringify(cfg, { indent: 2 }) + "\n");
}

export function configSet(args: string[]): Result {
  if (args.length === 0)
    return err("usage: animica-node config set <key=value> [key=value …]");
  const cfg = loadNodeConfig();
  const next: Record<string, unknown> = { ...cfg };
  for (const kv of args) {
    const eq = kv.indexOf("=");
    if (eq <= 0) return err(`bad key=value pair: ${kv}`);
    const k = kv.slice(0, eq);
    const v = kv.slice(eq + 1);
    if (k in next) {
      const current = (next as Record<string, unknown>)[k];
      if (typeof current === "number") next[k] = Number.parseFloat(v);
      else if (typeof current === "boolean") next[k] = v === "true" || v === "1";
      else next[k] = v;
    } else {
      next[k] = v;
    }
  }
  writeNodeConfig(next as unknown as NodeConfig);
  return ok(safeStringify(next, { indent: 2 }) + "\n");
}

export function walletPath(): Result {
  const cfg = loadNodeConfig();
  const path = `${cfg.dataDir}/wallet`;
  return ok(`${path}\n`);
}

export async function minerStatus(): Promise<Result> {
  const agentCfg = loadAgentConfig().config;
  const identity = detectMinerIdentity(agentCfg);
  const live = await probeMinerLive(identity).catch(() => null);
  return ok(
    safeStringify(
      {
        identity,
        live,
        recommendation:
          live?.metricsReachable && live?.hashrate
            ? `Active miner detected (hashrate=${live.hashrate}). Recommend resourceMode=miner-priority for animica-node.`
            : "No active miner detected.",
      },
      { indent: 2 },
    ) + "\n",
  );
}

/** Used by `animica-agent doctor` to align RPC URLs / chain id. */
export function exposedDiscovery(): { rpcUrl: string; chainId: number; pid: number | null; alive: boolean } | null {
  const cfg = loadNodeConfig();
  const paths = getDaemonPaths();
  const pid = readPid(paths);
  const alive = pid !== null && isAlive(pid);
  return {
    rpcUrl: `http://127.0.0.1:${cfg.rpcPort}/rpc`,
    chainId: cfg.chainId,
    pid,
    alive,
  };
}

export function balanceLink(addr?: string): Result {
  // Suggests the agent flow without duplicating it.
  const cfg = loadNodeConfig();
  const url = `http://127.0.0.1:${cfg.rpcPort}/rpc`;
  return ok(
    [
      `Tip: animica-node does not own wallet lookups directly to avoid duplicating agent flows.`,
      `Run: animica-agent balance ${addr ?? "<address>"}    (RPC=${url})`,
      `Or:  animica-node rpc call animica_getBalance ${addr ?? "<address>"} "latest"`,
      `formatANM helper available: ${formatANM(1_000_000_000_000_000_000n)} ANM`,
    ].join("\n") + "\n",
  );
}
