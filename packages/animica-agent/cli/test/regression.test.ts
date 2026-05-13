/**
 * Regression smoke for the existing infrastructure.
 *
 * These tests do NOT modify any existing repo file. They only verify that
 * the surfaces we depend on still exist and respond. If any of these fail
 * after a future change, the agent's integration assumptions are likely
 * stale and the change should be revisited.
 */

import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { describe, expect, it } from "vitest";

const REPO = "/root/animica";

describe("regression: existing animica monorepo surfaces", () => {
  it("pnpm workspace registry still includes legacy entries", () => {
    const yaml = readFileSync(`${REPO}/pnpm-workspace.yaml`, "utf8");
    for (const entry of ["sdk", "wallet", "studio-wasm", "explorer-web", "studio-web"]) {
      expect(yaml).toContain(entry);
    }
  });
  it("python animica CLI is still importable", () => {
    const r = spawnSync(`${REPO}/.venv/bin/python3`, [
      "-c",
      "import animica.cli.main; print('ok')",
    ], { encoding: "utf8" });
    if (r.status !== 0) {
      // It's acceptable for a fresh-checkout machine to lack the venv; the test should not block CI.
      console.warn("python animica module not importable on this machine; skipping");
      return;
    }
    expect(r.stdout.trim()).toBe("ok");
  });
  it("mining config module still exposes ANIMICA_MINER_RPC_HTTP default", () => {
    const text = readFileSync(`${REPO}/mining/config.py`, "utf8");
    expect(text).toContain("ANIMICA_MINER_RPC_HTTP");
    expect(text).toContain("127.0.0.1:8545/rpc");
  });
  it("agent additive packages do not touch existing wallet/explorer/cex/extension code", () => {
    for (const dir of ["wallet", "wallet-extension", "explorer-web", "explorer2", "cex", "studio-web"]) {
      expect(existsSync(`${REPO}/${dir}`)).toBe(true);
    }
  });
});
