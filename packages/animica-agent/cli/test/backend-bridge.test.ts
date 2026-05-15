/**
 * backend-bridge tests.
 *
 * Validate that the agent's backend resolver finds:
 *   1. a managed runtime install under ANIMICA_RUNTIME_HOME
 *   2. an ANIMICA_NODE_BIN override
 *   3. nothing → legacy-python fallback (without actually invoking python)
 */

import { mkdtempSync, mkdirSync, writeFileSync, rmSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { hasUsableBackend, resolveAgentBackend, runBackendCli } from "../src/backend-bridge.js";

describe("backend-bridge", () => {
  let prevHome: string | undefined;
  let prevBin: string | undefined;
  let prevPath: string | undefined;
  let prevPython: string | undefined;
  let tmp: string;

  beforeEach(() => {
    prevHome = process.env.ANIMICA_RUNTIME_HOME;
    prevBin = process.env.ANIMICA_NODE_BIN;
    prevPath = process.env.PATH;
    prevPython = process.env.ANIMICA_AGENT_PYTHON;
    tmp = mkdtempSync(join(tmpdir(), "agent-be-"));
    process.env.ANIMICA_RUNTIME_HOME = tmp;
    delete process.env.ANIMICA_NODE_BIN;
    // Empty PATH so the `which animica` probe can't accidentally match a real binary.
    process.env.PATH = "/nonexistent-no-which-target";
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
    if (prevHome === undefined) delete process.env.ANIMICA_RUNTIME_HOME;
    else process.env.ANIMICA_RUNTIME_HOME = prevHome;
    if (prevBin === undefined) delete process.env.ANIMICA_NODE_BIN;
    else process.env.ANIMICA_NODE_BIN = prevBin;
    if (prevPath === undefined) delete process.env.PATH;
    else process.env.PATH = prevPath;
    if (prevPython === undefined) delete process.env.ANIMICA_AGENT_PYTHON;
    else process.env.ANIMICA_AGENT_PYTHON = prevPython;
  });

  it("returns the env-override backend when ANIMICA_NODE_BIN is set", () => {
    const fake = join(tmp, "fake-bin");
    writeFileSync(fake, "#!/bin/sh\necho fake\n");
    chmodSync(fake, 0o755);
    process.env.ANIMICA_NODE_BIN = fake;
    const b = resolveAgentBackend()!;
    expect(b.source).toBe("env-override");
    expect(b.command).toBe(fake);
    expect(hasUsableBackend()).toBe(true);
  });

  it("returns the managed backend when a current.json + marker exist", () => {
    // Simulate a successful animica-node install for the current host platform.
    const p =
      process.platform === "win32"
        ? "win32"
        : process.platform === "darwin"
          ? "darwin"
          : "linux";
    const a = process.arch === "x64" ? "x64" : process.arch === "arm64" ? "arm64" : process.arch;
    const platformKey = `${p}-${a}`;
    const versionsDir = join(tmp, "versions", `stable-0.1.10-${platformKey}`);
    mkdirSync(join(versionsDir, "bin"), { recursive: true });
    const entry = process.platform === "win32" ? "bin/animica.cmd" : "bin/animica";
    const binPath = join(versionsDir, entry);
    writeFileSync(binPath, "#!/bin/sh\necho managed-stub\n");
    if (process.platform !== "win32") chmodSync(binPath, 0o755);
    writeFileSync(
      join(versionsDir, ".animica-runtime.json"),
      JSON.stringify({
        channel: "stable",
        version: "0.1.10",
        platformKey,
        installedAt: new Date().toISOString(),
        installDir: versionsDir,
        entry,
      }),
    );
    writeFileSync(
      join(tmp, "current.json"),
      JSON.stringify({
        schema: 1,
        active: { channel: "stable", version: "0.1.10", platformKey },
        history: [],
      }),
    );
    const b = resolveAgentBackend()!;
    expect(b.source).toBe("managed");
    expect(b.command).toBe(binPath);
    expect(hasUsableBackend()).toBe(true);
  });

  it("falls through to the legacy-python backend when nothing else resolves (but reports it as not 'usable')", () => {
    process.env.ANIMICA_AGENT_PYTHON = "python-that-does-not-exist-xyz";
    const b = resolveAgentBackend()!;
    expect(b.source).toBe("legacy-python");
    expect(hasUsableBackend()).toBe(false);
  });

  it("requireManaged returns null when there is no managed install", () => {
    const b = resolveAgentBackend({ requireManaged: true });
    expect(b).toBeNull();
  });

  it("runBackendCli passes through stdout, stderr, status from the resolved backend", () => {
    // Use a shell stub as the env-override binary that prints args.
    const fake = join(tmp, "echo-args");
    writeFileSync(
      fake,
      "#!/bin/sh\necho stdout-from-stub \"$@\"\necho err-from-stub >&2\nexit 0\n",
    );
    chmodSync(fake, 0o755);
    process.env.ANIMICA_NODE_BIN = fake;
    const r = runBackendCli(["wallet", "list"]);
    expect(r.status).toBe(0);
    expect(r.stdout).toContain("stdout-from-stub wallet list");
    expect(r.stderr).toContain("err-from-stub");
    expect(r.backend.source).toBe("env-override");
  });
});
