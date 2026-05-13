import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { DEFAULT_CONFIG, loadConfig, writeProjectConfig } from "../src/config.js";

describe("config", () => {
  let dir: string;
  let savedEnv: NodeJS.ProcessEnv;
  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "agent-cfg-"));
    // Drop a marker so findRepoRoot stops here instead of walking up into shared /tmp state.
    mkdirSync(join(dir, ".git"), { recursive: true });
    savedEnv = { ...process.env };
    // Pin every env that would otherwise reach into the user's home and pollute tests.
    process.env.ANIMICA_AGENT_HOME = join(dir, "user");
    process.env.ANIMICA_NODE_CONFIG = join(dir, "absent-node.json");
    for (const key of Object.keys(process.env)) {
      if (key.startsWith("ANIMICA_AGENT_") && key !== "ANIMICA_AGENT_HOME") delete process.env[key];
      if (key.startsWith("ANIMICA_MINER_") || key.startsWith("ANIMICA_POOL_")) delete process.env[key];
      if (key === "ANIMICA_RPC_URL" || key === "ANIMICA_CHAIN_ID") delete process.env[key];
    }
  });
  afterEach(() => {
    process.env = savedEnv;
    rmSync(dir, { recursive: true, force: true });
  });

  it("returns defaults in an empty workspace", () => {
    const r = loadConfig({ cwd: dir });
    expect(r.config.rpcUrl).toBe(DEFAULT_CONFIG.rpcUrl);
    expect(r.config.minerMode).toBe("auto");
  });

  it("env overrides win over file but not over explicit overrides", () => {
    process.env.ANIMICA_AGENT_RPC_URL = "http://example/rpc";
    const r = loadConfig({ cwd: dir });
    expect(r.config.rpcUrl).toBe("http://example/rpc");
    expect(r.sources.env).toBe(true);
    const r2 = loadConfig({ cwd: dir, overrides: { rpcUrl: "http://override/rpc" } });
    expect(r2.config.rpcUrl).toBe("http://override/rpc");
  });

  it("writes project config and rounds-trips", () => {
    const { paths } = loadConfig({ cwd: dir });
    writeProjectConfig(paths, { rpcUrl: "http://saved/rpc", minerAddress: "anm1xxxxxxxxxxxx" });
    const r = loadConfig({ cwd: dir });
    expect(r.config.rpcUrl).toBe("http://saved/rpc");
    expect(r.config.minerAddress).toBe("anm1xxxxxxxxxxxx");
  });

  it("layers a discovered animica-node config", () => {
    process.env.ANIMICA_NODE_CONFIG = join(dir, "node.json");
    writeFileSync(process.env.ANIMICA_NODE_CONFIG, JSON.stringify({ rpcPort: 9999, chainId: 42 }));
    const r = loadConfig({ cwd: dir });
    expect(r.config.rpcUrl).toBe("http://127.0.0.1:9999/rpc");
    expect(r.config.chainId).toBe("42");
  });
});
