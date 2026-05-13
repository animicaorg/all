/**
 * animica-node configuration.
 *
 * We deliberately keep this separate from agent-core's AgentConfig so the
 * node package is usable without installing the agent. They overlap on
 * rpcUrl/chainId, and the agent automatically discovers the node config
 * when present (see `discoverNodeConfig`).
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { safeParse, safeStringify } from "@animica/agent-core";

export type ResourceMode = "balanced" | "miner-priority" | "agent-priority";

export interface NodeConfig {
  network: "mainnet" | "testnet" | "devnet" | "local-devnet";
  chainId: number;
  rpcPort: number;
  p2pPort: number;
  metricsPort: number;
  dataDir: string;
  snapshotDir?: string;
  peerSeeds?: string[];
  bootstrap?: boolean;
  logLevel: "debug" | "info" | "warn" | "error";
  /** When true, propagates miner-safe scheduling to the node runtime. */
  minerSafeMode: boolean;
  resourceMode: ResourceMode;
}

export const DEFAULT_NODE_CONFIG: NodeConfig = {
  network: "local-devnet",
  chainId: 1,
  rpcPort: 8545,
  p2pPort: 30303,
  metricsPort: 9106,
  dataDir: join(homedir(), ".animica", "node", "data"),
  logLevel: "info",
  bootstrap: true,
  minerSafeMode: false,
  resourceMode: "balanced",
};

export function getNodeConfigPath(): string {
  return process.env.ANIMICA_NODE_CONFIG ?? join(homedir(), ".animica", "node", "node.json");
}

export function loadNodeConfig(): NodeConfig {
  const path = getNodeConfigPath();
  if (!existsSync(path)) return { ...DEFAULT_NODE_CONFIG };
  try {
    const j = safeParse<Partial<NodeConfig>>(readFileSync(path, "utf8"));
    return { ...DEFAULT_NODE_CONFIG, ...j };
  } catch {
    return { ...DEFAULT_NODE_CONFIG };
  }
}

export function writeNodeConfig(cfg: NodeConfig): NodeConfig {
  const path = getNodeConfigPath();
  mkdirSync(join(path, ".."), { recursive: true });
  writeFileSync(path, safeStringify(cfg, { indent: 2 }) + "\n", "utf8");
  return cfg;
}

/** Used by @animica/agent-core discovery to align RPC URLs. */
export function discoverNodeConfig(): { rpcUrl: string; chainId: number } | null {
  const path = getNodeConfigPath();
  if (!existsSync(path)) return null;
  try {
    const j = safeParse<NodeConfig>(readFileSync(path, "utf8"));
    return { rpcUrl: `http://127.0.0.1:${j.rpcPort ?? DEFAULT_NODE_CONFIG.rpcPort}/rpc`, chainId: j.chainId ?? 1 };
  } catch {
    return null;
  }
}
