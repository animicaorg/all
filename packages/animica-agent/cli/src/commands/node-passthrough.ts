/**
 * `animica-agent node ...` — convenience aliases that delegate to the
 * separately-published `animica-node` binary when present. Keeps the agent
 * CLI as the single entry point for end users without re-implementing node
 * orchestration here.
 */

import { spawnSync } from "node:child_process";

import { loadConfig, probeNode, safeStringify } from "@animica/agent-core";

import { boolFlag } from "../args.js";
import { c, fail, header, info, kv, ok, warn } from "../output.js";

function resolveNodeBin(): string {
  return process.env.ANIMICA_NODE_BIN ?? "animica-node";
}

function execNode(args: string[]): { status: number; stdout: string; stderr: string } {
  const r = spawnSync(resolveNodeBin(), args, { encoding: "utf8" });
  return { status: r.status ?? 1, stdout: r.stdout ?? "", stderr: r.stderr ?? "" };
}

export function runNodeSetup(options: Record<string, string | boolean>): number {
  const r = execNode(["init"]);
  process.stdout.write(r.stdout);
  if (r.stderr) process.stderr.write(r.stderr);
  if (r.status !== 0) {
    fail(
      `animica-node not found on PATH (or init failed). Install with: npm install -g animica-node`,
    );
    return 1;
  }
  if (boolFlag(options, "json", false)) {
    process.stdout.write(safeStringify({ status: r.status }, { indent: 2 }) + "\n");
  }
  return 0;
}

export async function runNodeStart(options: Record<string, string | boolean>): Promise<number> {
  // First check whether a node is already reachable; if so, skip.
  const { config } = loadConfig();
  const probe = await probeNode(config.rpcUrl).catch(() => null);
  if (probe?.reachable) {
    ok(`node already reachable at ${config.rpcUrl}`);
    if (boolFlag(options, "json", false)) {
      process.stdout.write(
        safeStringify({ alreadyRunning: true, rpcUrl: config.rpcUrl, chainId: probe.chainId?.toString() }, { indent: 2 }) + "\n",
      );
    }
    return 0;
  }
  const r = execNode(["start"]);
  process.stdout.write(r.stdout);
  if (r.stderr) process.stderr.write(r.stderr);
  if (r.status !== 0) {
    fail("animica-node start failed (or animica-node is not installed).");
    info(c.dim("Install with: npm install -g animica-node"));
    return 1;
  }
  return 0;
}

export async function runNodeStatus(options: Record<string, string | boolean>): Promise<number> {
  const { config } = loadConfig();
  const probe = await probeNode(config.rpcUrl).catch(() => null);
  const json = boolFlag(options, "json", false);
  const data = {
    rpcUrl: config.rpcUrl,
    chainId: config.chainId,
    reachable: probe?.reachable ?? false,
    observedChainId: probe?.chainId?.toString(),
    blockNumber: probe?.blockNumber?.toString(),
    clientVersion: probe?.clientVersion,
  };
  if (json) {
    process.stdout.write(safeStringify(data, { indent: 2 }) + "\n");
    return data.reachable ? 0 : 1;
  }
  header("Animica node");
  kv([
    ["rpcUrl", data.rpcUrl],
    ["chainId", data.chainId],
    ["reachable", data.reachable ? c.green("yes") : c.red("no")],
    ["observedChainId", data.observedChainId ?? "—"],
    ["blockNumber", data.blockNumber ?? "—"],
    ["clientVersion", data.clientVersion ?? "—"],
  ]);
  if (!data.reachable) {
    warn("node not reachable. Start it with `animica-agent node start` or `animica-node start`.");
  }
  return data.reachable ? 0 : 1;
}

export function runNodeLogs(options: Record<string, string | boolean>): number {
  const r = execNode(boolFlag(options, "follow", false) ? ["logs", "-f"] : ["logs"]);
  process.stdout.write(r.stdout);
  if (r.stderr) process.stderr.write(r.stderr);
  return r.status;
}

export function runMineStart(options: Record<string, string | boolean>): number {
  // Friendly alias for `animica-agent miner start`. We don't import miner-runtime
  // here to keep this module standalone; we re-exec the agent CLI itself.
  const argv = ["miner", "start"];
  if (boolFlag(options, "once", false)) argv.push("--once");
  if (boolFlag(options, "json", false)) argv.push("--json");
  const r = spawnSync(process.execPath, [process.argv[1], ...argv], { encoding: "utf8" });
  process.stdout.write(r.stdout ?? "");
  if (r.stderr) process.stderr.write(r.stderr);
  return r.status ?? 1;
}

export function runMineStatus(options: Record<string, string | boolean>): number {
  const argv = ["miner", "status"];
  if (boolFlag(options, "json", false)) argv.push("--json");
  const r = spawnSync(process.execPath, [process.argv[1], ...argv], { encoding: "utf8" });
  process.stdout.write(r.stdout ?? "");
  if (r.stderr) process.stderr.write(r.stderr);
  return r.status ?? 1;
}
