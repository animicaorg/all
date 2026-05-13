import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const BIN = new URL("../bin/animica-node.mjs", import.meta.url).pathname;

function run(args: string[], env: NodeJS.ProcessEnv = {}): { code: number; stdout: string; stderr: string } {
  const r = spawnSync(process.execPath, [BIN, ...args], { encoding: "utf8", env: { ...process.env, ...env } });
  return { code: r.status ?? 0, stdout: r.stdout, stderr: r.stderr };
}

describe("animica-node CLI", () => {
  it("prints help", () => {
    const r = run(["--help"]);
    expect(r.code).toBe(0);
    for (const verb of ["init", "start", "stop", "status", "doctor", "rpc", "peers", "reset", "config"]) {
      expect(r.stdout).toContain(verb);
    }
  });

  it("init writes a config to ANIMICA_NODE_CONFIG", () => {
    const dir = mkdtempSync(join(tmpdir(), "animica-node-"));
    const cfgPath = join(dir, "node.json");
    const r = run(["init"], { ANIMICA_NODE_CONFIG: cfgPath });
    expect(r.code).toBe(0);
    expect(r.stdout).toContain(cfgPath);
    rmSync(dir, { recursive: true });
  });

  it("config show round-trips", () => {
    const dir = mkdtempSync(join(tmpdir(), "animica-node-"));
    const cfgPath = join(dir, "node.json");
    run(["init"], { ANIMICA_NODE_CONFIG: cfgPath });
    const r = run(["config", "show"], { ANIMICA_NODE_CONFIG: cfgPath });
    expect(r.code).toBe(0);
    expect(r.stdout).toContain("rpcPort");
    rmSync(dir, { recursive: true });
  });
});
