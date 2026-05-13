/**
 * Router golden tests. These run the CLI without arguments / with --version
 * and assert stable surface text the way an end user would see it.
 */

import { spawnSync } from "node:child_process";
import { describe, expect, it } from "vitest";

const BIN = new URL("../bin/animica-agent.mjs", import.meta.url).pathname;

function run(args: string[], cwd = process.cwd()): { code: number; stdout: string; stderr: string } {
  const r = spawnSync(process.execPath, [BIN, ...args], { cwd, encoding: "utf8" });
  return { code: r.status ?? 0, stdout: r.stdout, stderr: r.stderr };
}

describe("animica-agent CLI router", () => {
  it("prints version", () => {
    const r = run(["--version"]);
    expect(r.code).toBe(0);
    expect(r.stdout.trim()).toMatch(/^\d+\.\d+\.\d+/);
  });
  it("prints help with stable subcommands", () => {
    const r = run(["--help"]);
    expect(r.code).toBe(0);
    for (const verb of ["init", "doctor", "status", "chat", "code", "diff", "apply", "rollback", "rpc", "wallet", "miner", "pricing", "budget", "receipts", "allowance", "jobs", "rewards", "leaderboard", "adapters", "release", "ui"]) {
      expect(r.stdout).toContain(verb);
    }
  });
  it("returns nonzero for unknown commands", () => {
    const r = run(["this-command-does-not-exist"]);
    expect(r.code).not.toBe(0);
  });
  it("estimates a cost without touching the network", () => {
    const r = run(["estimate", "code-task", "--premium", "--input", "1000", "--output", "2000"]);
    expect(r.code).toBe(0);
    expect(r.stdout).toMatch(/ANM/);
    expect(r.stdout).toMatch(/premium/);
  });
});
