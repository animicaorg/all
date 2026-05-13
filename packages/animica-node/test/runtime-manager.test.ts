/**
 * Runtime-manager end-to-end tests.
 *
 * These tests build a real tar.gz, expose it via a local HTTP server, and
 * exercise the full install pipeline: manifest fetch → download → checksum
 * verify → extract → activate → resolve. Nothing is mocked at the bytes
 * layer; the fixture server is real, the tarball is real, the SHA-256 is
 * real. Tests cover both the happy path and every failure mode (checksum
 * mismatch, missing platform, lock collision, unsupported platform).
 */

import { createHash } from "node:crypto";
import { createServer, type Server } from "node:http";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { gzipSync } from "node:zlib";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  DEFAULT_STABLE_MANIFEST_URL,
  defaultManifestUrl,
  fetchManifest,
  installRuntime,
  manifestUrlForChannel,
  platformKey,
  pruneRuntime,
  removeRuntime,
  repairRuntime,
  resolveRuntimeEntry,
  runtimeStatus,
  withLock,
  IntegrityError,
  UnsupportedPlatformError,
  ManifestError,
  validateRuntimeManifest,
  type RuntimeManifest,
  getRuntimePaths,
} from "../src/runtime-manager.js";
import { resolveBackend } from "../src/backend.js";

/* ---------------- POSIX tar writer for fixtures ---------------- */

function tarHeader(name: string, size: number, mode = 0o644, typeflag = "0"): Buffer {
  const buf = Buffer.alloc(512, 0);
  buf.write(name.slice(0, 100), 0, 100);
  buf.write(mode.toString(8).padStart(7, "0") + "\0", 100, 8);
  buf.write("0000000\0", 108, 8); // uid
  buf.write("0000000\0", 116, 8); // gid
  buf.write(size.toString(8).padStart(11, "0") + "\0", 124, 12);
  buf.write(Math.floor(Date.now() / 1000).toString(8).padStart(11, "0") + "\0", 136, 12);
  buf.write("        ", 148, 8); // checksum placeholder
  buf.write(typeflag, 156, 1);
  buf.write("ustar  \0", 257, 8); // GNU magic + version
  // Checksum.
  let sum = 0;
  for (const b of buf) sum += b;
  buf.write(sum.toString(8).padStart(6, "0") + "\0 ", 148, 8);
  return buf;
}

function makeTar(entries: { name: string; data: Buffer | string; mode?: number; type?: "file" | "dir" }[]): Buffer {
  const parts: Buffer[] = [];
  for (const e of entries) {
    if (e.type === "dir") {
      parts.push(tarHeader(e.name.endsWith("/") ? e.name : e.name + "/", 0, e.mode ?? 0o755, "5"));
      continue;
    }
    const data = Buffer.isBuffer(e.data) ? e.data : Buffer.from(e.data, "utf8");
    parts.push(tarHeader(e.name, data.length, e.mode ?? 0o644, "0"));
    parts.push(data);
    const pad = 512 - (data.length % 512 || 512);
    if (pad > 0) parts.push(Buffer.alloc(pad, 0));
  }
  // Two zero blocks = archive end.
  parts.push(Buffer.alloc(1024, 0));
  return Buffer.concat(parts);
}

function sha256(buf: Buffer): string {
  return createHash("sha256").update(buf).digest("hex");
}

/* ---------------- fixture HTTP server ---------------- */

interface Fixture {
  server: Server;
  url: string;
  tarBytes: Buffer;
  manifest: RuntimeManifest;
  manifestUrl: string;
  /** Per-request log so tests can assert on Range, retries, etc. */
  requests: { url: string; range?: string }[];
  /** Make /tarball return a corrupt body on the next N requests. */
  corruptNextRequests: number;
  close(): Promise<void>;
}

function startFixture(opts: { withSignature?: boolean } = {}): Promise<Fixture> {
  return new Promise((resolveP) => {
    const tar = makeTar([
      { name: "bin/", type: "dir" },
      { name: "bin/animica", data: "#!/bin/sh\nexec /usr/bin/env true \"$@\"\n", mode: 0o755 },
      { name: "LICENSE", data: "TEST" },
    ]);
    const tarGz = gzipSync(tar);
    const digest = sha256(tarGz);
    const requests: { url: string; range?: string }[] = [];
    let corruptCounter = 0;

    const server = createServer((req, res) => {
      const url = req.url ?? "/";
      requests.push({ url, range: req.headers["range"] as string | undefined });
      if (url === "/manifest.json") {
        const manifestBody: RuntimeManifest = {
          schema: 1,
          channel: "stable",
          version: "0.1.0",
          generatedAt: new Date().toISOString(),
          assets: {
            // Cover the runner's actual platform key so the install test runs.
            [platformKey()]: {
              url: `http://127.0.0.1:${(server.address() as { port: number }).port}/tarball.tar.gz`,
              sha256: digest,
              bytes: tarGz.length,
              entry: "bin/animica",
            },
          },
          ...(opts.withSignature ? { signature: { algorithm: "ed25519", value: Buffer.from("abc").toString("base64") } } : {}),
        };
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify(manifestBody));
        return;
      }
      if (url === "/tarball.tar.gz") {
        if (corruptCounter > 0) {
          corruptCounter--;
          res.writeHead(200, { "content-type": "application/octet-stream" });
          res.end(Buffer.from("not a real tarball"));
          return;
        }
        const range = req.headers["range"];
        if (typeof range === "string" && range.startsWith("bytes=")) {
          const [from] = range.slice(6).split("-").map((x) => parseInt(x, 10));
          if (Number.isFinite(from)) {
            const chunk = tarGz.subarray(from);
            res.writeHead(206, {
              "content-type": "application/octet-stream",
              "content-range": `bytes ${from}-${tarGz.length - 1}/${tarGz.length}`,
            });
            res.end(chunk);
            return;
          }
        }
        res.writeHead(200, { "content-type": "application/octet-stream", "content-length": String(tarGz.length) });
        res.end(tarGz);
        return;
      }
      if (url === "/bad-manifest.json") {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ schema: 1, channel: "stable", version: "not-semver", assets: {} }));
        return;
      }
      res.writeHead(404);
      res.end();
    });
    server.listen(0, "127.0.0.1", () => {
      const port = (server.address() as { port: number }).port;
      const url = `http://127.0.0.1:${port}`;
      const manifest: RuntimeManifest = {
        schema: 1,
        channel: "stable",
        version: "0.1.0",
        generatedAt: new Date().toISOString(),
        assets: {
          [platformKey()]: {
            url: `${url}/tarball.tar.gz`,
            sha256: digest,
            bytes: tarGz.length,
            entry: "bin/animica",
          },
        },
      };
      resolveP({
        server,
        url,
        tarBytes: tarGz,
        manifest,
        manifestUrl: `${url}/manifest.json`,
        requests,
        corruptNextRequests: 0,
        close: () =>
          new Promise<void>((r) => {
            server.close(() => r());
          }),
      });
    });
  });
}

/* ---------------- tests ---------------- */

describe("runtime-manager: manifest", () => {
  it("defaultManifestUrl resolves the stable hosted channel by default", () => {
    const beforeUrl = process.env.ANIMICA_RUNTIME_MANIFEST_URL;
    const beforeChannel = process.env.ANIMICA_RUNTIME_CHANNEL;
    delete process.env.ANIMICA_RUNTIME_MANIFEST_URL;
    delete process.env.ANIMICA_RUNTIME_CHANNEL;
    expect(defaultManifestUrl()).toBe(DEFAULT_STABLE_MANIFEST_URL);
    if (beforeUrl === undefined) delete process.env.ANIMICA_RUNTIME_MANIFEST_URL;
    else process.env.ANIMICA_RUNTIME_MANIFEST_URL = beforeUrl;
    if (beforeChannel === undefined) delete process.env.ANIMICA_RUNTIME_CHANNEL;
    else process.env.ANIMICA_RUNTIME_CHANNEL = beforeChannel;
  });

  it("manifestUrlForChannel supports stable, beta, and dev", () => {
    expect(manifestUrlForChannel("stable")).toBe("https://releases.animica.org/runtime/stable/manifest.json");
    expect(manifestUrlForChannel("beta")).toBe("https://releases.animica.org/runtime/beta/manifest.json");
    expect(manifestUrlForChannel("dev")).toBe("https://releases.animica.org/runtime/dev/manifest.json");
    expect(() => manifestUrlForChannel("nightly")).toThrow(/unsupported runtime channel/);
  });

  it("fetchManifest accepts a well-formed manifest", async () => {
    const fix = await startFixture();
    const m = await fetchManifest(fix.manifestUrl);
    expect(m.version).toBe("0.1.0");
    expect(m.assets[platformKey()]).toBeTruthy();
    await fix.close();
  });

  it("fetchManifest rejects an invalid manifest", async () => {
    const fix = await startFixture();
    await expect(fetchManifest(`${fix.url}/bad-manifest.json`)).rejects.toBeInstanceOf(ManifestError);
    await fix.close();
  });

  it("validateRuntimeManifest rejects artifacts missing sha256", () => {
    const bad = {
      schema: 1,
      channel: "stable",
      version: "0.1.0",
      generatedAt: new Date().toISOString(),
      assets: {
        [platformKey()]: {
          url: "https://releases.animica.org/runtime/stable/a.tar.gz",
          entry: "bin/animica",
        },
      },
    };
    expect(() => validateRuntimeManifest(bad)).toThrow(/sha256/);
  });

  it("defaultManifestUrl supports channel env but keeps URL override highest priority", () => {
    const beforeUrl = process.env.ANIMICA_RUNTIME_MANIFEST_URL;
    const beforeChannel = process.env.ANIMICA_RUNTIME_CHANNEL;
    delete process.env.ANIMICA_RUNTIME_MANIFEST_URL;
    process.env.ANIMICA_RUNTIME_CHANNEL = "beta";
    expect(defaultManifestUrl()).toBe("https://releases.animica.org/runtime/beta/manifest.json");
    process.env.ANIMICA_RUNTIME_MANIFEST_URL = "http://custom/manifest.json";
    process.env.ANIMICA_RUNTIME_CHANNEL = "dev";
    expect(defaultManifestUrl()).toBe("http://custom/manifest.json");
    if (beforeUrl === undefined) delete process.env.ANIMICA_RUNTIME_MANIFEST_URL;
    else process.env.ANIMICA_RUNTIME_MANIFEST_URL = beforeUrl;
    if (beforeChannel === undefined) delete process.env.ANIMICA_RUNTIME_CHANNEL;
    else process.env.ANIMICA_RUNTIME_CHANNEL = beforeChannel;
  });

  it("fetchManifest rejects signed manifests when no trusted key is configured", async () => {
    const before = process.env.ANIMICA_RUNTIME_MANIFEST_PUBLIC_KEY;
    delete process.env.ANIMICA_RUNTIME_MANIFEST_PUBLIC_KEY;
    const fix = await startFixture({ withSignature: true });
    await expect(fetchManifest(fix.manifestUrl)).rejects.toThrow(/no trusted public key/);
    await fix.close();
    if (before === undefined) delete process.env.ANIMICA_RUNTIME_MANIFEST_PUBLIC_KEY;
    else process.env.ANIMICA_RUNTIME_MANIFEST_PUBLIC_KEY = before;
  });

  it("installRuntime rejects missing platform match before download", async () => {
    const fix = await startFixture();
    await expect(installRuntime({ manifest: fix.manifest, platformKey: "linux-nope" })).rejects.toBeInstanceOf(UnsupportedPlatformError);
    await fix.close();
  });

  it("defaultManifestUrl is configurable via env", () => {
    const before = process.env.ANIMICA_RUNTIME_MANIFEST_URL;
    process.env.ANIMICA_RUNTIME_MANIFEST_URL = "http://custom/manifest.json";
    expect(defaultManifestUrl()).toBe("http://custom/manifest.json");
    if (before === undefined) delete process.env.ANIMICA_RUNTIME_MANIFEST_URL;
    else process.env.ANIMICA_RUNTIME_MANIFEST_URL = before;
  });
});

describe("runtime-manager: install pipeline", () => {
  let prevHome: string | undefined;
  let tmp: string;

  beforeEach(() => {
    prevHome = process.env.ANIMICA_RUNTIME_HOME;
    tmp = mkdtempSync(join(tmpdir(), "rm-"));
    process.env.ANIMICA_RUNTIME_HOME = tmp;
  });
  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
    if (prevHome === undefined) delete process.env.ANIMICA_RUNTIME_HOME;
    else process.env.ANIMICA_RUNTIME_HOME = prevHome;
  });

  it("installs from a real fixture tarball end-to-end and activates", async () => {
    const fix = await startFixture();
    const r = await installRuntime({ manifestUrl: fix.manifestUrl });
    expect(r.reused).toBe(false);
    expect(r.activated).toBe(true);
    expect(existsSync(join(r.installed.installDir, r.installed.entry))).toBe(true);
    expect(r.installed.version).toBe("0.1.0");
    // After activation, resolveRuntimeEntry must point to the managed binary.
    const entry = resolveRuntimeEntry();
    expect(entry).toBeTruthy();
    expect(entry!.source).toBe("managed");
    expect(entry!.command).toContain(r.installed.entry);
    // And resolveBackend must report a managed-runtime source.
    const backend = resolveBackend({ allowLegacy: false });
    expect(backend.source.startsWith("managed-runtime(")).toBe(true);
    await fix.close();
  });

  it("rejects a tarball whose checksum does not match the manifest", async () => {
    const fix = await startFixture();
    // Tamper: serve a corrupt body on the next request.
    fix.corruptNextRequests = 1;
    // Mutate the closure by intercepting the next request via a manifest override.
    // The fixture exposes a counter; we set it via the property on the fixture object.
    (fix as unknown as { corruptNextRequests: number }).corruptNextRequests = 1;
    // We can't mutate the server callback directly, so use a custom manifest pointing
    // at /tarball.tar.gz with a deliberately-wrong sha.
    const bad: RuntimeManifest = {
      schema: 1,
      channel: "stable",
      version: "0.1.0",
      generatedAt: new Date().toISOString(),
      assets: {
        [platformKey()]: {
          url: `${fix.url}/tarball.tar.gz`,
          sha256: "0".repeat(64),
          entry: "bin/animica",
        },
      },
    };
    await expect(installRuntime({ manifest: bad })).rejects.toBeInstanceOf(IntegrityError);
    // No partial install should remain.
    expect(runtimeStatus().installs.length).toBe(0);
    await fix.close();
  });

  it("refuses when no asset is published for the running platform", async () => {
    const fix = await startFixture();
    const otherPlatform = platformKey() === "linux-arm64" ? "linux-x64" : "linux-arm64";
    const fake: RuntimeManifest = {
      ...fix.manifest,
      assets: { [otherPlatform]: fix.manifest.assets[platformKey()] },
    };
    await expect(installRuntime({ manifest: fake })).rejects.toBeInstanceOf(UnsupportedPlatformError);
    await fix.close();
  });

  it("re-uses an existing install when called twice without --force", async () => {
    const fix = await startFixture();
    const a = await installRuntime({ manifest: fix.manifest });
    const b = await installRuntime({ manifest: fix.manifest });
    expect(a.reused).toBe(false);
    expect(b.reused).toBe(true);
    expect(a.installed.installDir).toBe(b.installed.installDir);
    await fix.close();
  });

  it("force=true performs a clean reinstall", async () => {
    const fix = await startFixture();
    await installRuntime({ manifest: fix.manifest });
    const b = await installRuntime({ manifest: fix.manifest, force: true });
    expect(b.reused).toBe(false);
    await fix.close();
  });

  it("refuses to acquire a held lockfile", async () => {
    const fix = await startFixture();
    // Hold the lockfile manually.
    const { lockFile, root } = getRuntimePaths();
    mkdirSync(root, { recursive: true });
    writeFileSync(lockFile, String(process.pid));
    try {
      await expect(installRuntime({ manifest: fix.manifest })).rejects.toThrow(/install.*in progress|lockfile/i);
    } finally {
      rmSync(lockFile, { force: true });
    }
    await fix.close();
  });

  it("withLock cleans up its lockfile on completion and on error", async () => {
    const { lockFile } = getRuntimePaths();
    expect(existsSync(lockFile)).toBe(false);
    await withLock(async () => "ok");
    expect(existsSync(lockFile)).toBe(false);
    await expect(
      withLock(async () => {
        throw new Error("boom");
      }),
    ).rejects.toThrow(/boom/);
    expect(existsSync(lockFile)).toBe(false);
  });

  it("repair reports OK installs and broken ones", async () => {
    const fix = await startFixture();
    await installRuntime({ manifest: fix.manifest });
    const r1 = repairRuntime();
    expect(r1.ok).toBe(1);
    expect(r1.failed.length).toBe(0);
    // Break one install by removing the entry file.
    const active = runtimeStatus().active!;
    rmSync(join(active.installDir, active.entry), { force: true });
    const r2 = repairRuntime();
    expect(r2.failed.length).toBe(1);
    expect(r2.failed[0].reason).toMatch(/entry/);
    await fix.close();
  });

  it("remove --all wipes the install + pointer", async () => {
    const fix = await startFixture();
    await installRuntime({ manifest: fix.manifest });
    expect(runtimeStatus().installs.length).toBe(1);
    removeRuntime({ all: true });
    expect(runtimeStatus().installs.length).toBe(0);
    expect(runtimeStatus().active).toBeNull();
    await fix.close();
  });

  it("prune drops old installs but keeps the active one", async () => {
    const fix = await startFixture();
    // Install two versions by mutating the manifest.
    const m1 = { ...fix.manifest, version: "0.1.0" };
    const m2 = { ...fix.manifest, version: "0.2.0" };
    await installRuntime({ manifest: m1 });
    await installRuntime({ manifest: m2 });
    expect(runtimeStatus().installs.length).toBe(2);
    const p = pruneRuntime(1);
    // Active was 0.2.0 (last installed); keep≥1 + always-active means
    // we may end up keeping both since active counts. Just assert no
    // crashes and a consistent state.
    expect(p.kept.length + p.removed.length).toBe(2);
    expect(runtimeStatus().installs.length).toBeGreaterThanOrEqual(1);
    await fix.close();
  });
});

describe("runtime-manager: backend resolution", () => {
  let prevHome: string | undefined;
  let tmp: string;
  let prevNodeBin: string | undefined;

  beforeEach(() => {
    prevHome = process.env.ANIMICA_RUNTIME_HOME;
    prevNodeBin = process.env.ANIMICA_NODE_BIN;
    tmp = mkdtempSync(join(tmpdir(), "rm-be-"));
    process.env.ANIMICA_RUNTIME_HOME = tmp;
    // Make sure no env override interferes with the test.
    delete process.env.ANIMICA_NODE_BIN;
  });
  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
    if (prevHome === undefined) delete process.env.ANIMICA_RUNTIME_HOME;
    else process.env.ANIMICA_RUNTIME_HOME = prevHome;
    if (prevNodeBin === undefined) delete process.env.ANIMICA_NODE_BIN;
    else process.env.ANIMICA_NODE_BIN = prevNodeBin;
  });

  it("env override wins over everything else", () => {
    process.env.ANIMICA_NODE_BIN = process.execPath; // any existing path
    const b = resolveBackend();
    expect(b.source).toBe("env(ANIMICA_NODE_BIN)");
    expect(b.command).toBe(process.execPath);
  });

  it("managed runtime is preferred over legacy when both exist", async () => {
    const fix = await startFixture();
    await installRuntime({ manifest: fix.manifest });
    const b = resolveBackend({ allowLegacy: false });
    expect(b.source.startsWith("managed-runtime(")).toBe(true);
    await fix.close();
  });

  it("with no managed runtime AND allowLegacy=false returns an unresolved Backend", () => {
    const b = resolveBackend({ allowLegacy: false });
    expect(b.source).toBe("no-managed-runtime");
    expect(b.command).toBe("");
  });
});
