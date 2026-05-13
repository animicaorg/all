/**
 * CLI surface for managed runtime operations:
 *   animica-node install-runtime [--manifest-url <url>] [--force]
 *   animica-node runtime status
 *   animica-node runtime repair
 *   animica-node runtime remove [--all|--channel <c> --version <v>]
 *   animica-node runtime prune [--keep N]
 *   animica-node doctor
 *
 * Output is human-readable by default and JSON-shaped when `--json` is set.
 */

import { safeStringify } from "@animica/agent-core";

import {
  defaultManifestUrl,
  installRuntime,
  platformKey,
  pruneRuntime,
  removeRuntime,
  repairRuntime,
  runtimeStatus,
  fetchManifest,
  NoManifestError,
  IntegrityError,
  ManifestError,
  UnsupportedPlatformError,
  LockHeldError,
} from "./runtime-manager.js";
import { resolveBackend } from "./backend.js";

export interface CmdResult {
  exit: number;
  output: string;
}

function parseFlag(args: string[], name: string): string | undefined {
  const eq = args.find((a) => a.startsWith(`--${name}=`));
  if (eq) return eq.slice(name.length + 3);
  const idx = args.indexOf(`--${name}`);
  if (idx !== -1 && idx + 1 < args.length && !args[idx + 1].startsWith("--")) return args[idx + 1];
  return undefined;
}

function boolFlag(args: string[], name: string): boolean {
  return args.includes(`--${name}`);
}

function ok(text: string, exit = 0): CmdResult {
  return { exit, output: text };
}
function err(text: string, exit = 1): CmdResult {
  return { exit, output: `error: ${text}\n` };
}

export async function runInstallRuntime(args: string[]): Promise<CmdResult> {
  const url = parseFlag(args, "manifest-url") ?? defaultManifestUrl();
  const force = boolFlag(args, "force");
  const json = boolFlag(args, "json");
  try {
    const r = await installRuntime({
      manifestUrl: url,
      force,
      onProgress: (info) => {
        if (json) return;
        if (info.phase === "download" && info.total) {
          process.stderr.write(
            `\rdownloading… ${Math.min(100, Math.floor(((info.bytes ?? 0) / info.total) * 100))}%`,
          );
        } else if (info.phase !== "download") {
          process.stderr.write(`\n${info.phase}…`);
        }
      },
    });
    if (!json) process.stderr.write("\n");
    if (json) return ok(safeStringify(r, { indent: 2 }) + "\n");
    return ok(
      [
        `${r.reused ? "reused" : "installed"} ${r.installed.channel}@${r.installed.version} (${r.installed.platformKey})`,
        `  installDir: ${r.installed.installDir}`,
        `  entry:      ${r.installed.entry}`,
        `  activated:  ${r.activated ? "yes" : "no"}`,
      ].join("\n") + "\n",
    );
  } catch (e) {
    const msg =
      e instanceof IntegrityError
        ? `integrity check failed: ${e.message}\nThe partial download was discarded; nothing was activated.`
        : e instanceof UnsupportedPlatformError
          ? e.message
          : e instanceof NoManifestError
            ? `${e.message}\nIf you're offline, set ANIMICA_RUNTIME_MANIFEST_URL to a local copy or use ANIMICA_NODE_BIN.`
            : e instanceof ManifestError
              ? `manifest is invalid: ${e.message}`
              : e instanceof LockHeldError
                ? e.message
                : (e as Error).message;
    return err(msg);
  }
}

export function runRuntimeStatus(args: string[]): CmdResult {
  const json = boolFlag(args, "json");
  const s = runtimeStatus();
  if (json) return ok(safeStringify(s, { indent: 2 }) + "\n");
  const lines: string[] = [];
  lines.push(`platform: ${s.platformKey}`);
  lines.push(`manifest: ${s.manifestUrl}`);
  lines.push(`root:     ${s.paths.root}`);
  if (s.active) {
    lines.push(`active:   ${s.active.channel}@${s.active.version} (${s.active.platformKey})`);
    lines.push(`  ${s.active.installDir}`);
  } else {
    lines.push("active:   (none)");
  }
  lines.push(`installs: ${s.installs.length}`);
  for (const i of s.installs) {
    lines.push(`  - ${i.channel}@${i.version} ${i.platformKey}  ${i.installedAt}`);
  }
  return ok(lines.join("\n") + "\n");
}

export function runRuntimeRepair(args: string[]): CmdResult {
  const json = boolFlag(args, "json");
  const r = repairRuntime();
  if (json) return ok(safeStringify(r, { indent: 2 }) + "\n");
  const lines = [
    `checked ${r.checked} install(s)`,
    `  ok:     ${r.ok}`,
    `  failed: ${r.failed.length}`,
  ];
  for (const f of r.failed) lines.push(`  ✗ ${f.dir}: ${f.reason}`);
  return ok(lines.join("\n") + "\n", r.failed.length > 0 ? 1 : 0);
}

export function runRuntimeRemove(args: string[]): CmdResult {
  const json = boolFlag(args, "json");
  const all = boolFlag(args, "all");
  const channel = parseFlag(args, "channel");
  const version = parseFlag(args, "version");
  if (!all && (!channel || !version)) {
    return err("usage: animica-node runtime remove --all | (--channel <c> --version <v>)");
  }
  const r = removeRuntime({ all, channel, version });
  if (json) return ok(safeStringify(r, { indent: 2 }) + "\n");
  if (r.removed.length === 0) return ok("nothing removed\n");
  return ok(`removed:\n${r.removed.map((p) => `  - ${p}`).join("\n")}\n`);
}

export function runRuntimePrune(args: string[]): CmdResult {
  const json = boolFlag(args, "json");
  const keep = Number.parseInt(parseFlag(args, "keep") ?? "2", 10);
  const r = pruneRuntime(Number.isFinite(keep) ? keep : 2);
  if (json) return ok(safeStringify(r, { indent: 2 }) + "\n");
  return ok(
    [`kept ${r.kept.length}, removed ${r.removed.length}`, ...r.removed.map((p) => `  - ${p}`)].join("\n") + "\n",
  );
}

export async function runRuntimeDoctor(args: string[]): Promise<CmdResult> {
  const json = boolFlag(args, "json");
  const s = runtimeStatus();
  const lines: string[] = [];
  // Manifest reachability (best-effort).
  let manifestStatus: { ok: boolean; error?: string; version?: string } = { ok: false };
  try {
    const m = await fetchManifest(s.manifestUrl, { timeoutMs: 5_000 });
    manifestStatus = { ok: true, version: `${m.channel}@${m.version}` };
  } catch (e) {
    manifestStatus = { ok: false, error: (e as Error).message?.slice(0, 200) };
  }
  // Effective backend (managed first, falling back to legacy only if necessary).
  const backendStrict = resolveBackend({ allowLegacy: false });
  const backendEffective = resolveBackend({ allowLegacy: true });
  const data = {
    platform: s.platformKey,
    runtimeRoot: s.paths.root,
    manifestUrl: s.manifestUrl,
    manifest: manifestStatus,
    activeInstall: s.active,
    installs: s.installs.length,
    backendStrict: backendStrict.command ? backendStrict : null,
    backendEffective,
  };
  if (json) return ok(safeStringify(data, { indent: 2 }) + "\n");
  lines.push(`platform:     ${data.platform}`);
  lines.push(`runtime root: ${data.runtimeRoot}`);
  lines.push(`manifest:     ${data.manifestUrl}`);
  lines.push(
    `manifest fetch: ${manifestStatus.ok ? `OK (${manifestStatus.version})` : `FAIL — ${manifestStatus.error ?? "unknown"}`}`,
  );
  lines.push(`active install: ${data.activeInstall ? `${data.activeInstall.channel}@${data.activeInstall.version}` : "(none)"}`);
  lines.push(`installs on disk: ${data.installs}`);
  lines.push(
    `backend (managed-only): ${data.backendStrict ? data.backendStrict.source : "(none)"}`,
  );
  lines.push(`backend (effective): ${data.backendEffective.source} → ${data.backendEffective.command || "(unresolved)"}`);
  return ok(lines.join("\n") + "\n");
}

/**
 * Used by `animica-node start` and friends: if there's no managed runtime
 * AND no legacy fallback resolves to an existing path, attempt to install
 * the runtime in-process before continuing.
 */
export async function ensureRuntimeOrInstall(args: { autoInstall?: boolean; manifestUrl?: string } = {}): Promise<{ ok: boolean; message?: string; backendSource: string }> {
  const eff = resolveBackend({ allowLegacy: true });
  // If env override or managed runtime resolves, we're good.
  if (eff.source.startsWith("env(") || eff.source.startsWith("managed-runtime(")) {
    return { ok: true, backendSource: eff.source };
  }
  // Legacy path resolution returns a non-empty command if it found something
  // on disk. Otherwise fall through to install.
  if (eff.source.startsWith("legacy(PATH)") || eff.source.startsWith(`legacy(${process.cwd()}`)) {
    return { ok: true, backendSource: eff.source };
  }
  if (args.autoInstall === false) {
    return {
      ok: false,
      backendSource: eff.source,
      message:
        "no managed runtime found and auto-install disabled. Run `animica-node install-runtime` first.",
    };
  }
  try {
    const r = await installRuntime({ manifestUrl: args.manifestUrl });
    return { ok: true, backendSource: `managed-runtime(${r.installed.channel}@${r.installed.version})` };
  } catch (e) {
    return {
      ok: false,
      backendSource: eff.source,
      message: `runtime auto-install failed: ${(e as Error).message}`,
    };
  }
}
