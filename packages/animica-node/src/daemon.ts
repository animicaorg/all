/**
 * Daemon manager.
 *
 * Tracks the running animica-node process in a small pidfile under
 * ~/.animica/node/. On start/stop we touch only this directory; we never
 * own or delete the user's node state directory.
 *
 * `start --foreground` simply re-execs the backend with stdio inheritance;
 * `start` (default) detaches the child and writes logs into the state dir.
 */

import { existsSync, mkdirSync, openSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { resolveBackend, runBackend, spawnBackend } from "./backend.js";
import type { NodeConfig } from "./config.js";

export interface DaemonPaths {
  /** ~/.animica/node */
  root: string;
  pidFile: string;
  logFile: string;
  errFile: string;
  configFile: string;
}

export function getDaemonPaths(): DaemonPaths {
  const root = process.env.ANIMICA_NODE_HOME
    ? process.env.ANIMICA_NODE_HOME
    : join(homedir(), ".animica", "node");
  return {
    root,
    pidFile: join(root, "node.pid"),
    logFile: join(root, "node.out.log"),
    errFile: join(root, "node.err.log"),
    configFile: join(root, "node.json"),
  };
}

export function readPid(paths: DaemonPaths = getDaemonPaths()): number | null {
  if (!existsSync(paths.pidFile)) return null;
  const raw = readFileSync(paths.pidFile, "utf8").trim();
  const pid = Number.parseInt(raw, 10);
  return Number.isFinite(pid) ? pid : null;
}

export function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

export interface StartOptions {
  config: NodeConfig;
  /** When true, run in foreground with inherited stdio. */
  foreground?: boolean;
  /** Additional args passed to the backend after `node up`. */
  extraArgs?: string[];
}

export interface StartResult {
  pid: number;
  detached: boolean;
  logFile: string;
  errFile: string;
  alreadyRunning: boolean;
  backendSource: string;
}

export function startDaemon(opts: StartOptions): StartResult {
  const paths = getDaemonPaths();
  mkdirSync(paths.root, { recursive: true });
  const existingPid = readPid(paths);
  if (existingPid && isAlive(existingPid)) {
    return {
      pid: existingPid,
      detached: true,
      logFile: paths.logFile,
      errFile: paths.errFile,
      alreadyRunning: true,
      backendSource: resolveBackend().source,
    };
  }
  const args = buildNodeArgs(opts.config, opts.extraArgs);
  const env = buildNodeEnv(opts.config);
  if (opts.foreground) {
    const r = runBackend({ args, env, inherit: true });
    return {
      pid: process.pid,
      detached: false,
      logFile: paths.logFile,
      errFile: paths.errFile,
      alreadyRunning: false,
      backendSource: r.backend.source,
    };
  }
  const out = openSync(paths.logFile, "a");
  const err = openSync(paths.errFile, "a");
  const child = spawnBackend({ args, env, detached: true });
  if (child.stdout) child.stdout.pipe(require("node:fs").createWriteStream(paths.logFile, { flags: "a", fd: out }));
  if (child.stderr) child.stderr.pipe(require("node:fs").createWriteStream(paths.errFile, { flags: "a", fd: err }));
  child.unref();
  if (typeof child.pid !== "number") throw new Error("failed to start backend (no pid)");
  writeFileSync(paths.pidFile, String(child.pid), "utf8");
  return {
    pid: child.pid,
    detached: true,
    logFile: paths.logFile,
    errFile: paths.errFile,
    alreadyRunning: false,
    backendSource: resolveBackend().source,
  };
}

export interface StopResult {
  stopped: boolean;
  pid?: number;
  reason?: string;
}

export function stopDaemon(timeoutMs = 5000): StopResult {
  const paths = getDaemonPaths();
  const pid = readPid(paths);
  if (!pid) return { stopped: false, reason: "no pidfile" };
  if (!isAlive(pid)) {
    rmSync(paths.pidFile, { force: true });
    return { stopped: true, pid, reason: "process already gone" };
  }
  try {
    process.kill(pid, "SIGTERM");
  } catch (err) {
    return { stopped: false, pid, reason: (err as Error).message };
  }
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!isAlive(pid)) break;
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 100);
  }
  if (isAlive(pid)) {
    try {
      process.kill(pid, "SIGKILL");
    } catch {
      /* ignore */
    }
  }
  rmSync(paths.pidFile, { force: true });
  return { stopped: true, pid };
}

function buildNodeArgs(cfg: NodeConfig, extra: string[] = []): string[] {
  const args = ["node", "up"];
  if (cfg.rpcPort) args.push("--rpc-port", String(cfg.rpcPort));
  if (cfg.p2pPort) args.push("--p2p-port", String(cfg.p2pPort));
  if (cfg.metricsPort) args.push("--metrics-port", String(cfg.metricsPort));
  if (cfg.dataDir) args.push("--data-dir", cfg.dataDir);
  if (cfg.snapshotDir) args.push("--snapshot-dir", cfg.snapshotDir);
  if (cfg.network) args.push("--network", cfg.network);
  if (cfg.logLevel) args.push("--log-level", cfg.logLevel);
  if (cfg.peerSeeds?.length) args.push("--peers", cfg.peerSeeds.join(","));
  if (cfg.bootstrap === false) args.push("--no-bootstrap");
  return [...args, ...extra];
}

function buildNodeEnv(cfg: NodeConfig): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  if (cfg.chainId !== undefined) env.ANIMICA_CHAIN_ID = String(cfg.chainId);
  if (cfg.rpcPort) env.ANIMICA_RPC_PORT = String(cfg.rpcPort);
  if (cfg.minerSafeMode) env.ANIMICA_NODE_MINER_SAFE = "1";
  if (cfg.resourceMode) env.ANIMICA_NODE_RESOURCE_MODE = cfg.resourceMode;
  return env;
}
