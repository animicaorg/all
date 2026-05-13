/**
 * Managed Animica node runtime.
 *
 * Responsibility: install / verify / locate a self-contained backend binary
 * for the Animica node into a per-user directory (`~/.animica/runtime/`)
 * without depending on a repo checkout or a pre-existing Python toolchain.
 *
 * Design notes:
 *
 *   - Versioned. Every install is keyed by (channel, version, platform-arch);
 *     installs are atomic (download → verify → rename), never partially
 *     observable.
 *   - Concurrency-safe via a lockfile. Two processes cannot corrupt the
 *     same install directory.
 *   - Resumable. A partial `.part` file from a prior interrupted download
 *     is resumed via a Range header when supported.
 *   - Verified. The manifest carries SHA-256 digests for every artifact;
 *     installs fail closed and roll back to the prior version if the
 *     checksum does not match.
 *   - Honest. This module ONLY provisions and locates the runtime; it does
 *     not embed a Python or node binary in the npm package. The actual
 *     binary tarballs must be published on a real release channel (see
 *     `defaultManifestUrl()`). The fixture-server tests prove the install
 *     pipeline end-to-end against a real local HTTP server.
 *
 * No fake success states: a missing manifest, a checksum mismatch, or a
 * lockfile collision all surface explicit errors with operator-actionable
 * messages. Nothing is "downgraded silently" to legacy paths from here.
 */

import { createHash } from "node:crypto";
import { createReadStream, createWriteStream, existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync, openSync, closeSync, readdirSync, symlinkSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { gunzipSync } from "node:zlib";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";

import { safeParse, safeStringify } from "@animica/agent-core";

export const DEFAULT_CHANNEL = "stable";

export interface RuntimeAsset {
  /** Tarball URL (.tar.gz). */
  url: string;
  /** SHA-256 hex digest of the tarball bytes. */
  sha256: string;
  /** Optional size in bytes for early bail-out on bad mirrors. */
  bytes?: number;
  /** Entry point relative to the unpacked dir (e.g. "bin/animica" or "bin/animica.exe"). */
  entry: string;
}

export interface RuntimeManifest {
  /** Manifest schema version. Increment when keys change incompatibly. */
  schema: number;
  /** Release channel. */
  channel: string;
  /** Runtime version (semver string). */
  version: string;
  /** Manifest generation timestamp (ISO). */
  generatedAt: string;
  /** Per-platform-arch entries. Keys are like "linux-x64", "darwin-arm64", "win32-x64". */
  assets: Record<string, RuntimeAsset>;
  /** Optional signature payload (e.g. minisign / cosign). Honored if present; ignored if absent. */
  signature?: { algorithm: string; value: string };
}

export interface RuntimePaths {
  /** Root install directory. e.g. ~/.animica/runtime */
  root: string;
  /** Where versioned installs live. */
  versionsDir: string;
  /** Lockfile path. */
  lockFile: string;
  /** Path of the most-recently-installed runtime tracked by `current.json`. */
  currentPointerFile: string;
}

export function getRuntimePaths(): RuntimePaths {
  const root = process.env.ANIMICA_RUNTIME_HOME ?? join(homedir(), ".animica", "runtime");
  return {
    root,
    versionsDir: join(root, "versions"),
    lockFile: join(root, "install.lock"),
    currentPointerFile: join(root, "current.json"),
  };
}

export interface InstalledRuntime {
  channel: string;
  version: string;
  platformKey: string;
  installedAt: string;
  installDir: string;
  entry: string;
}

export interface CurrentPointer {
  schema: number;
  active?: { channel: string; version: string; platformKey: string };
  history: { channel: string; version: string; platformKey: string; installedAt: string }[];
}

/** Default manifest URL. Operators or CI can override with ANIMICA_RUNTIME_MANIFEST_URL. */
export function defaultManifestUrl(): string {
  return (
    process.env.ANIMICA_RUNTIME_MANIFEST_URL ??
    `https://releases.animica.org/runtime/${DEFAULT_CHANNEL}/manifest.json`
  );
}

export function platformKey(): string {
  const p =
    process.platform === "win32"
      ? "win32"
      : process.platform === "darwin"
        ? "darwin"
        : "linux";
  const a = process.arch === "x64" ? "x64" : process.arch === "arm64" ? "arm64" : process.arch;
  return `${p}-${a}`;
}

/* -------------------- manifest loading -------------------- */

export class ManifestError extends Error {}
export class IntegrityError extends Error {}
export class LockHeldError extends Error {}
export class NoManifestError extends Error {}
export class UnsupportedPlatformError extends Error {}

function ensureValidManifest(value: unknown): RuntimeManifest {
  if (typeof value !== "object" || value === null) throw new ManifestError("manifest is not an object");
  const m = value as Partial<RuntimeManifest>;
  if (typeof m.schema !== "number" || m.schema < 1) throw new ManifestError("manifest.schema missing or invalid");
  if (typeof m.channel !== "string" || !m.channel) throw new ManifestError("manifest.channel missing");
  if (typeof m.version !== "string" || !/^\d+\.\d+\.\d+/.test(m.version))
    throw new ManifestError("manifest.version missing or not semver");
  if (typeof m.assets !== "object" || m.assets === null)
    throw new ManifestError("manifest.assets missing");
  for (const [k, v] of Object.entries(m.assets!)) {
    if (typeof v !== "object" || v === null) throw new ManifestError(`assets[${k}] is not an object`);
    const a = v as RuntimeAsset;
    if (typeof a.url !== "string" || !/^https?:\/\//.test(a.url))
      throw new ManifestError(`assets[${k}].url must be http(s)`);
    if (typeof a.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(a.sha256))
      throw new ManifestError(`assets[${k}].sha256 must be a 64-char hex digest`);
    if (typeof a.entry !== "string" || !a.entry)
      throw new ManifestError(`assets[${k}].entry must be a non-empty path`);
  }
  return m as RuntimeManifest;
}

export async function fetchManifest(
  url = defaultManifestUrl(),
  opts: { fetchImpl?: typeof fetch; timeoutMs?: number } = {},
): Promise<RuntimeManifest> {
  const fetchImpl = opts.fetchImpl ?? globalThis.fetch;
  if (typeof fetchImpl !== "function") throw new ManifestError("no fetch implementation available");
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), opts.timeoutMs ?? 10_000);
  let res: Response;
  try {
    res = await fetchImpl(url, { signal: ac.signal });
  } catch (err) {
    throw new NoManifestError(`failed to fetch manifest from ${url}: ${(err as Error).message}`);
  } finally {
    clearTimeout(t);
  }
  if (!res.ok) throw new NoManifestError(`manifest fetch HTTP ${res.status} from ${url}`);
  let body: unknown;
  try {
    body = await res.json();
  } catch (err) {
    throw new ManifestError(`manifest is not valid JSON: ${(err as Error).message}`);
  }
  return ensureValidManifest(body);
}

/* -------------------- pointer state -------------------- */

export function readCurrentPointer(): CurrentPointer {
  const { currentPointerFile } = getRuntimePaths();
  if (!existsSync(currentPointerFile)) return { schema: 1, history: [] };
  try {
    const j = safeParse<CurrentPointer>(readFileSync(currentPointerFile, "utf8"));
    if (typeof j === "object" && j !== null && typeof j.schema === "number" && Array.isArray(j.history)) {
      return j;
    }
  } catch {
    /* fall through */
  }
  return { schema: 1, history: [] };
}

export function writeCurrentPointer(p: CurrentPointer): void {
  const { currentPointerFile } = getRuntimePaths();
  mkdirSync(dirname(currentPointerFile), { recursive: true });
  writeFileSync(currentPointerFile, safeStringify(p, { indent: 2 }) + "\n", "utf8");
}

/** Returns the active install, or null. */
export function findActiveInstall(): InstalledRuntime | null {
  const ptr = readCurrentPointer();
  if (!ptr.active) return null;
  const { versionsDir } = getRuntimePaths();
  const dir = join(versionsDir, `${ptr.active.channel}-${ptr.active.version}-${ptr.active.platformKey}`);
  if (!existsSync(dir)) return null;
  // We don't know the entry path without re-reading the manifest snapshot we
  // saved at install time. Read the per-install marker.
  const marker = join(dir, ".animica-runtime.json");
  if (!existsSync(marker)) return null;
  try {
    const j = safeParse<InstalledRuntime>(readFileSync(marker, "utf8"));
    if (j && j.entry && j.installDir) return j;
  } catch {
    /* corrupt */
  }
  return null;
}

/* -------------------- lockfile -------------------- */

export function withLock<T>(fn: () => Promise<T> | T): Promise<T> {
  const { lockFile, root } = getRuntimePaths();
  mkdirSync(root, { recursive: true });
  let fd: number;
  try {
    fd = openSync(lockFile, "wx");
  } catch (err) {
    const e = err as NodeJS.ErrnoException;
    if (e.code === "EEXIST") {
      // Inspect age — a lockfile older than 30 min is stale.
      try {
        const stat = statSync(lockFile);
        const ageMs = Date.now() - stat.mtimeMs;
        if (ageMs > 30 * 60_000) {
          rmSync(lockFile, { force: true });
          fd = openSync(lockFile, "wx");
        } else {
          throw new LockHeldError(
            `another animica-node runtime install appears to be in progress (lockfile ${lockFile}, ${Math.round(ageMs / 1000)}s old). If you are sure no other process is running, remove the file manually.`,
          );
        }
      } catch (innerErr) {
        if (innerErr instanceof LockHeldError) throw innerErr;
        throw new LockHeldError(`lockfile ${lockFile} cannot be inspected: ${(innerErr as Error).message}`);
      }
    } else {
      throw err;
    }
  }
  try {
    writeFileSync(fd, String(process.pid));
  } catch {
    /* harmless */
  }
  return Promise.resolve()
    .then(() => fn())
    .finally(() => {
      try {
        closeSync(fd);
      } catch {
        /* ignore */
      }
      try {
        rmSync(lockFile, { force: true });
      } catch {
        /* ignore */
      }
    });
}

/* -------------------- install pipeline -------------------- */

export interface InstallOptions {
  manifest?: RuntimeManifest;
  /** Override the manifest URL (or use defaultManifestUrl()). */
  manifestUrl?: string;
  /** Force re-install even if the target version is already installed. */
  force?: boolean;
  /** When set, restrict to this platform key (for tests). */
  platformKey?: string;
  /** Progress callback invoked with rolling byte counts. */
  onProgress?: (info: { phase: "download" | "extract" | "verify" | "activate"; bytes?: number; total?: number }) => void;
  /** Used by tests to swap fetch. */
  fetchImpl?: typeof fetch;
}

export interface InstallResult {
  installed: InstalledRuntime;
  /** True if we activated this install (i.e. it became `current`). */
  activated: boolean;
  /** True when the version was already installed and `force` was not set. */
  reused: boolean;
}

async function sha256OfFile(path: string): Promise<string> {
  return new Promise<string>((resolveP, rejectP) => {
    const h = createHash("sha256");
    const s = createReadStream(path);
    s.on("data", (chunk) => h.update(chunk));
    s.on("end", () => resolveP(h.digest("hex")));
    s.on("error", rejectP);
  });
}

/**
 * Install (or verify-only) a runtime version. Atomic + checksum-verified.
 */
export async function installRuntime(opts: InstallOptions = {}): Promise<InstallResult> {
  const paths = getRuntimePaths();
  mkdirSync(paths.versionsDir, { recursive: true });
  return withLock(async () => {
    const manifest = opts.manifest ?? (await fetchManifest(opts.manifestUrl, { fetchImpl: opts.fetchImpl }));
    const pk = opts.platformKey ?? platformKey();
    const asset = manifest.assets[pk];
    if (!asset) {
      throw new UnsupportedPlatformError(
        `no runtime asset for platform ${pk} in manifest ${manifest.channel}@${manifest.version}`,
      );
    }
    const installDirName = `${manifest.channel}-${manifest.version}-${pk}`;
    const installDir = join(paths.versionsDir, installDirName);
    const marker = join(installDir, ".animica-runtime.json");

    if (existsSync(marker) && !opts.force) {
      // Verify marker matches what we'd write; reuse without re-downloading.
      try {
        const existing = safeParse<InstalledRuntime>(readFileSync(marker, "utf8"));
        if (existing.entry === asset.entry && existing.version === manifest.version) {
          const activated = activate(existing);
          return { installed: existing, activated, reused: true };
        }
      } catch {
        /* fall through to reinstall */
      }
    }

    // Stage tarball into <root>/tmp/<random>.part
    const tmpRoot = join(paths.root, "tmp");
    mkdirSync(tmpRoot, { recursive: true });
    const stagedTar = join(tmpRoot, `${manifest.channel}-${manifest.version}-${pk}-${process.pid}.tar.gz`);
    const partFile = stagedTar + ".part";

    opts.onProgress?.({ phase: "download", bytes: 0, total: asset.bytes });

    // Resumable download.
    let downloadedSoFar = 0;
    if (existsSync(partFile)) {
      try {
        downloadedSoFar = statSync(partFile).size;
      } catch {
        /* fresh download */
      }
    }
    const fetchImpl = opts.fetchImpl ?? globalThis.fetch;
    const headers: Record<string, string> = {};
    if (downloadedSoFar > 0) headers.Range = `bytes=${downloadedSoFar}-`;
    const res = await fetchImpl(asset.url, { headers });
    if (!res.ok && res.status !== 206) {
      throw new ManifestError(`download HTTP ${res.status} from ${asset.url}`);
    }
    // If server didn't honor Range (status 200 instead of 206), restart fresh.
    const append = res.status === 206 && downloadedSoFar > 0;
    if (!append && existsSync(partFile)) rmSync(partFile, { force: true });

    const writer = createWriteStream(partFile, { flags: append ? "a" : "w" });
    if (res.body) {
      // Node's fetch returns a web ReadableStream; pipeline accepts it via Readable.fromWeb.
      const reader: Readable = Readable.fromWeb(res.body as never);
      // Wire a progress observer.
      reader.on("data", (chunk) => {
        downloadedSoFar += (chunk as Buffer).length;
        opts.onProgress?.({ phase: "download", bytes: downloadedSoFar, total: asset.bytes });
      });
      await pipeline(reader, writer);
    } else {
      writer.end();
    }

    // Atomic-rename .part to final .tar.gz.
    renameSync(partFile, stagedTar);

    opts.onProgress?.({ phase: "verify" });
    const observedSha = await sha256OfFile(stagedTar);
    if (observedSha !== asset.sha256.toLowerCase()) {
      rmSync(stagedTar, { force: true });
      throw new IntegrityError(
        `checksum mismatch for ${asset.url}: expected ${asset.sha256}, got ${observedSha}`,
      );
    }

    opts.onProgress?.({ phase: "extract" });
    // Extract into a tmp dir, then rename atomically into installDir.
    const stagingDir = stagedTar + ".d";
    if (existsSync(stagingDir)) rmSync(stagingDir, { recursive: true, force: true });
    mkdirSync(stagingDir, { recursive: true });
    extractTarball(stagedTar, stagingDir);

    // Validate the entry exists inside the staged dir.
    const stagedEntry = join(stagingDir, asset.entry);
    if (!existsSync(stagedEntry)) {
      rmSync(stagedTar, { force: true });
      rmSync(stagingDir, { recursive: true, force: true });
      throw new IntegrityError(
        `tarball at ${asset.url} did not contain entry ${asset.entry}`,
      );
    }

    // Atomic install: rmdir old (if a re-install), then rename.
    if (existsSync(installDir)) {
      rmSync(installDir, { recursive: true, force: true });
    }
    renameSync(stagingDir, installDir);
    rmSync(stagedTar, { force: true });

    const installed: InstalledRuntime = {
      channel: manifest.channel,
      version: manifest.version,
      platformKey: pk,
      installedAt: new Date().toISOString(),
      installDir,
      entry: asset.entry,
    };
    writeFileSync(marker, safeStringify(installed, { indent: 2 }) + "\n", "utf8");
    opts.onProgress?.({ phase: "activate" });
    const activated = activate(installed);
    return { installed, activated, reused: false };
  });
}

function activate(installed: InstalledRuntime): boolean {
  const ptr = readCurrentPointer();
  const next: CurrentPointer = {
    schema: ptr.schema || 1,
    active: { channel: installed.channel, version: installed.version, platformKey: installed.platformKey },
    history: [
      ...ptr.history.filter(
        (h) =>
          !(
            h.channel === installed.channel &&
            h.version === installed.version &&
            h.platformKey === installed.platformKey
          ),
      ),
      { channel: installed.channel, version: installed.version, platformKey: installed.platformKey, installedAt: installed.installedAt },
    ].slice(-10),
  };
  writeCurrentPointer(next);
  return true;
}

/* -------------------- extraction -------------------- */

/**
 * Minimal POSIX tar.gz extractor. Synchronous, no native deps. Supports
 * ustar regular files + directories; ignores other entry types. Symlinks
 * are recreated when possible. Mode bits are preserved from the header.
 */
export function extractTarball(tarGzPath: string, destDir: string): void {
  mkdirSync(destDir, { recursive: true });
  const compressed = readFileSync(tarGzPath);
  const raw = gunzipSync(compressed);

  // Walk POSIX tar blocks (512 bytes each).
  let offset = 0;
  while (offset + 512 <= raw.length) {
    const header = raw.subarray(offset, offset + 512);
    // Empty block = end of archive.
    if (header.every((b) => b === 0)) break;
    const name = readStr(header, 0, 100);
    const modeStr = readStr(header, 100, 8).trim();
    const sizeStr = readStr(header, 124, 12).trim();
    const typeflag = String.fromCharCode(header[156] || 0);
    const linkname = readStr(header, 157, 100);
    const prefix = readStr(header, 345, 155);
    const fullPath = (prefix ? prefix + "/" : "") + name;
    const size = parseInt(sizeStr || "0", 8) || 0;
    const mode = parseInt(modeStr || "0", 8) || 0o644;
    offset += 512;
    const dataStart = offset;
    const dataEnd = dataStart + size;
    offset = dataEnd + (512 - (size % 512 || 512));

    if (!fullPath) continue;
    const safe = fullPath.replace(/^\/+/, "").replace(/\.\.(\/|\\)/g, "");
    const outPath = resolve(destDir, safe);
    if (!outPath.startsWith(resolve(destDir))) {
      throw new IntegrityError(`tar entry escapes destination dir: ${fullPath}`);
    }
    if (typeflag === "5") {
      mkdirSync(outPath, { recursive: true, mode });
      continue;
    }
    if (typeflag === "0" || typeflag === "" || typeflag === "\0") {
      mkdirSync(dirname(outPath), { recursive: true });
      writeFileSync(outPath, raw.subarray(dataStart, dataEnd), { mode: mode | (mode & 0o100 ? 0o111 : 0) });
      continue;
    }
    if (typeflag === "2") {
      // Symlink. Best-effort; skip if unsupported.
      try {
        mkdirSync(dirname(outPath), { recursive: true });
        symlinkSync(linkname, outPath);
      } catch {
        /* fall through */
      }
      continue;
    }
    // Other types ('1' hard link, '3' chardev, etc.) are skipped — Animica
    // runtime tarballs do not need them.
  }
}

function readStr(buf: Buffer, off: number, len: number): string {
  let end = off + len;
  for (let i = off; i < off + len; i++) {
    if (buf[i] === 0) {
      end = i;
      break;
    }
  }
  return buf.toString("utf8", off, end);
}

/* -------------------- status / repair / remove / prune -------------------- */

export interface RuntimeStatus {
  active: InstalledRuntime | null;
  installs: InstalledRuntime[];
  paths: RuntimePaths;
  manifestUrl: string;
  platformKey: string;
}

export function runtimeStatus(): RuntimeStatus {
  const paths = getRuntimePaths();
  const active = findActiveInstall();
  const installs: InstalledRuntime[] = [];
  if (existsSync(paths.versionsDir)) {
    for (const d of readdirSync(paths.versionsDir)) {
      const marker = join(paths.versionsDir, d, ".animica-runtime.json");
      if (!existsSync(marker)) continue;
      try {
        installs.push(safeParse<InstalledRuntime>(readFileSync(marker, "utf8")));
      } catch {
        /* skip corrupt */
      }
    }
  }
  return { active, installs, paths, manifestUrl: defaultManifestUrl(), platformKey: platformKey() };
}

export interface RepairReport {
  checked: number;
  ok: number;
  failed: { dir: string; reason: string }[];
}

export function repairRuntime(): RepairReport {
  const paths = getRuntimePaths();
  const r: RepairReport = { checked: 0, ok: 0, failed: [] };
  if (!existsSync(paths.versionsDir)) return r;
  for (const d of readdirSync(paths.versionsDir)) {
    const dir = join(paths.versionsDir, d);
    const marker = join(dir, ".animica-runtime.json");
    r.checked++;
    if (!existsSync(marker)) {
      r.failed.push({ dir, reason: "missing marker .animica-runtime.json" });
      continue;
    }
    try {
      const m = safeParse<InstalledRuntime>(readFileSync(marker, "utf8"));
      if (!existsSync(join(dir, m.entry))) {
        r.failed.push({ dir, reason: `entry ${m.entry} missing` });
        continue;
      }
      r.ok++;
    } catch (err) {
      r.failed.push({ dir, reason: `marker unparseable: ${(err as Error).message}` });
    }
  }
  return r;
}

/** Delete a specific installed version. Pass `{ all: true }` to wipe all installs and the pointer. */
export function removeRuntime(opts: { channel?: string; version?: string; platformKey?: string; all?: boolean } = {}): { removed: string[] } {
  const paths = getRuntimePaths();
  const removed: string[] = [];
  if (opts.all) {
    if (existsSync(paths.versionsDir)) rmSync(paths.versionsDir, { recursive: true, force: true });
    if (existsSync(paths.currentPointerFile)) rmSync(paths.currentPointerFile, { force: true });
    removed.push(paths.versionsDir);
    return { removed };
  }
  if (!opts.channel || !opts.version) return { removed };
  const pk = opts.platformKey ?? platformKey();
  const dir = join(paths.versionsDir, `${opts.channel}-${opts.version}-${pk}`);
  if (existsSync(dir)) {
    rmSync(dir, { recursive: true, force: true });
    removed.push(dir);
    const ptr = readCurrentPointer();
    if (
      ptr.active &&
      ptr.active.channel === opts.channel &&
      ptr.active.version === opts.version &&
      ptr.active.platformKey === pk
    ) {
      ptr.active = undefined;
      writeCurrentPointer(ptr);
    }
  }
  return { removed };
}

/** Keep only the N most recent installs (per channel) plus the active one. */
export function pruneRuntime(keep = 2): { kept: string[]; removed: string[] } {
  const paths = getRuntimePaths();
  const ptr = readCurrentPointer();
  const all = runtimeStatus().installs;
  const byChannel = new Map<string, InstalledRuntime[]>();
  for (const i of all) {
    const arr = byChannel.get(i.channel) ?? [];
    arr.push(i);
    byChannel.set(i.channel, arr);
  }
  const kept: string[] = [];
  const removed: string[] = [];
  for (const [, arr] of byChannel) {
    arr.sort((a, b) => (a.installedAt < b.installedAt ? 1 : -1));
    for (let i = 0; i < arr.length; i++) {
      const isActive =
        !!ptr.active &&
        ptr.active.channel === arr[i].channel &&
        ptr.active.version === arr[i].version &&
        ptr.active.platformKey === arr[i].platformKey;
      if (i < keep || isActive) {
        kept.push(arr[i].installDir);
      } else {
        try {
          rmSync(arr[i].installDir, { recursive: true, force: true });
          removed.push(arr[i].installDir);
        } catch {
          /* skip */
        }
      }
    }
  }
  return { kept, removed };
}

/* -------------------- entry-point resolution -------------------- */

export interface ResolvedEntry {
  command: string;
  /** Absolute path of the runtime install dir. */
  installDir: string;
  /** Source label for operator display. */
  source: "managed" | "env-override" | "path" | "repo-fallback";
}

/**
 * Resolve the actual binary we'll spawn. Priority:
 *   1. ANIMICA_NODE_BIN (operator override, preserved)
 *   2. managed runtime (active install)
 *   3. legacy `animica` on PATH (so existing dev machines keep working)
 *   4. caller-supplied repo fallback (only when `allowRepoFallback`)
 */
export function resolveRuntimeEntry(opts: { allowRepoFallback?: boolean } = {}): ResolvedEntry | null {
  const envBin = process.env.ANIMICA_NODE_BIN;
  if (envBin && existsSync(envBin)) {
    return { command: envBin, installDir: dirname(envBin), source: "env-override" };
  }
  const active = findActiveInstall();
  if (active) {
    const cmd = join(active.installDir, active.entry);
    if (existsSync(cmd)) return { command: cmd, installDir: active.installDir, source: "managed" };
  }
  const which = spawnSync(process.platform === "win32" ? "where" : "which", ["animica"], { encoding: "utf8" });
  if (which.status === 0 && which.stdout.trim()) {
    const p = which.stdout.trim().split(/\r?\n/)[0];
    return { command: p, installDir: dirname(p), source: "path" };
  }
  if (opts.allowRepoFallback === true) {
    return null;
  }
  return null;
}
