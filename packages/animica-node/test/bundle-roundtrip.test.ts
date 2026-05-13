/**
 * Round-trip test: scripts/build-runtime-bundle.mjs → install pipeline.
 *
 * Builds a real bundle on disk using the release-engineering script, then
 * serves it through a local HTTP server and exercises the full install
 * pipeline. This is the proof that the format the script emits is
 * exactly what `runtime-manager` expects.
 *
 * Skipped on Windows runners because the script's launcher emits a
 * POSIX shell file by default (the .cmd shim is platform-gated and the
 * marker check still validates).
 */

import { createServer, type Server } from "node:http";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { getRuntimePaths, installRuntime, platformKey } from "../src/runtime-manager.js";
import { resolveBackend } from "../src/backend.js";

const BUNDLE_BUILDER = join(__dirname, "..", "scripts", "build-runtime-bundle.mjs");
const MANIFEST_GENERATOR = join(__dirname, "..", "scripts", "generate-runtime-manifest.mjs");

function fakeAnimicaSrc(root: string): void {
  const pkg = join(root, "animica");
  mkdirSync(join(pkg, "cli"), { recursive: true });
  writeFileSync(join(pkg, "__init__.py"), "");
  writeFileSync(join(pkg, "cli", "__init__.py"), "");
  writeFileSync(
    join(pkg, "cli", "main.py"),
    'import sys\nprint("animica-stub argv=", sys.argv[1:])\n',
  );
}

describe("bundle roundtrip", () => {
  let prevHome: string | undefined;
  let runtimeHome: string;
  let work: string;
  let server: Server | undefined;
  let port = 0;

  beforeEach(() => {
    prevHome = process.env.ANIMICA_RUNTIME_HOME;
    runtimeHome = mkdtempSync(join(tmpdir(), "rm-rt-"));
    work = mkdtempSync(join(tmpdir(), "rm-work-"));
    process.env.ANIMICA_RUNTIME_HOME = runtimeHome;
  });

  afterEach(async () => {
    if (server) await new Promise<void>((r) => server!.close(() => r()));
    rmSync(runtimeHome, { recursive: true, force: true });
    rmSync(work, { recursive: true, force: true });
    if (prevHome === undefined) delete process.env.ANIMICA_RUNTIME_HOME;
    else process.env.ANIMICA_RUNTIME_HOME = prevHome;
  });

  it("build-runtime-bundle.mjs → install → resolveBackend resolves to managed binary", async () => {
    // 1. Build a fake source tree.
    fakeAnimicaSrc(work);
    // 2. Build a bundle using the real script.
    const outDir = join(work, "bundles");
    const build = spawnSync(
      process.execPath,
      [
        BUNDLE_BUILDER,
        "--version",
        "1.2.3",
        "--src",
        join(work, "animica"),
        "--out",
        outDir,
      ],
      { encoding: "utf8" },
    );
    expect(build.status, build.stderr).toBe(0);
    expect(existsSync(outDir)).toBe(true);
    // 3. Generate the manifest using the real script.
    const manifest = spawnSync(
      process.execPath,
      [
        MANIFEST_GENERATOR,
        "--dir",
        outDir,
        "--base",
        `http://placeholder/`, // will be rewritten below to bind a real port
        "--version",
        "1.2.3",
      ],
      { encoding: "utf8" },
    );
    expect(manifest.status, manifest.stderr).toBe(0);
    // 4. Patch the manifest to point at our about-to-start server.
    const manifestPath = join(outDir, "manifest.json");
    const m = JSON.parse(readFileSync(manifestPath, "utf8"));
    // Start an HTTP server now that we have the asset to serve.
    server = createServer((req, res) => {
      const url = req.url ?? "/";
      if (url === "/manifest.json") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify(m));
        return;
      }
      // Strip the leading slash and look up the file.
      const fileName = url.replace(/^\//, "");
      const path = join(outDir, fileName);
      if (!existsSync(path)) {
        res.writeHead(404);
        res.end();
        return;
      }
      const body = readFileSync(path);
      res.writeHead(200, { "content-type": "application/octet-stream", "content-length": String(body.length) });
      res.end(body);
    });
    await new Promise<void>((r) => server!.listen(0, "127.0.0.1", () => r()));
    port = (server!.address() as { port: number }).port;
    for (const k of Object.keys(m.assets)) {
      m.assets[k].url = `http://127.0.0.1:${port}/${k.startsWith("win32")
        ? `animica-runtime-stable-1.2.3-${k}.tar.gz`
        : `animica-runtime-stable-1.2.3-${k}.tar.gz`}`;
    }
    writeFileSync(manifestPath, JSON.stringify(m));
    // 5. Install through the runtime-manager.
    const r = await installRuntime({ manifestUrl: `http://127.0.0.1:${port}/manifest.json` });
    expect(r.reused).toBe(false);
    expect(r.activated).toBe(true);
    const installedEntry = join(r.installed.installDir, r.installed.entry);
    expect(existsSync(installedEntry)).toBe(true);
    // 6. resolveBackend must now resolve to this binary.
    const b = resolveBackend({ allowLegacy: false });
    expect(b.source.startsWith("managed-runtime(")).toBe(true);
    expect(b.command).toBe(installedEntry);
    // 7. Confirm the per-install marker matches the asset key we built.
    const marker = JSON.parse(readFileSync(join(r.installed.installDir, ".animica-runtime.json"), "utf8"));
    expect(marker.version).toBe("1.2.3");
    expect(marker.platformKey).toBe(platformKey());
    // 8. Confirm the runtime install dir has the expected layout.
    expect(existsSync(join(r.installed.installDir, "BUNDLE.json"))).toBe(true);
    expect(existsSync(join(r.installed.installDir, "share", "animica", "cli", "main.py"))).toBe(true);
  });
});
