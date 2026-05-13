/**
 * Backend detection & invocation for animica-node.
 *
 * Resolution order (after this rewrite):
 *
 *   1. ANIMICA_NODE_BIN=<path>            (operator override; preserved)
 *   2. Managed runtime under ~/.animica/runtime/                  ← NEW
 *      Installed via the runtime-manager subsystem; this is the default
 *      production path for npm-only users.
 *   3. installed `animica` shell command  (legacy compatibility)
 *   4. <repoRoot>/.venv/bin/python -m animica.cli.main   (legacy dev path)
 *   5. python3 -m animica.cli.main        (final fallback, last resort)
 *
 * The legacy paths (#3-#5) remain so existing dev workstations keep
 * working, but they are NO LONGER the primary path for new users. The
 * `Backend.source` field surfaces exactly which step matched so doctor
 * and status can show what's actually in use.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";
import { spawn, spawnSync, type ChildProcess, type SpawnOptions } from "node:child_process";

import { findRepoRoot } from "@animica/agent-core";

import { findActiveInstall } from "./runtime-manager.js";

export interface Backend {
  kind: "wrapped-cli" | "python-module";
  command: string;
  args: string[];
  cwd: string;
  source: string;
}

export interface ResolveOptions {
  cwd?: string;
  /** When false, skip legacy repo / venv / PATH fallbacks (used by `runtime status`). */
  allowLegacy?: boolean;
}

export function resolveBackend(opts: ResolveOptions = {}): Backend {
  const cwd = opts.cwd ?? process.cwd();
  // 1. Operator env override.
  const envBin = process.env.ANIMICA_NODE_BIN;
  if (envBin && existsSync(envBin)) {
    return { kind: "wrapped-cli", command: envBin, args: [], cwd, source: "env(ANIMICA_NODE_BIN)" };
  }
  // 2. Managed runtime (production default for npm-only users).
  const managed = findActiveInstall();
  if (managed) {
    const cmd = join(managed.installDir, managed.entry);
    if (existsSync(cmd)) {
      return {
        kind: "wrapped-cli",
        command: cmd,
        args: [],
        cwd,
        source: `managed-runtime(${managed.channel}@${managed.version})`,
      };
    }
  }
  if (opts.allowLegacy === false) {
    // Caller asked us not to fall back. Return a probe that points at a
    // non-existent file; the caller is responsible for handling it.
    return { kind: "wrapped-cli", command: "", args: [], cwd, source: "no-managed-runtime" };
  }
  // 3. Legacy: animica on PATH.
  const which = spawnSync(process.platform === "win32" ? "where" : "which", ["animica"], { encoding: "utf8" });
  if (which.status === 0 && which.stdout.trim()) {
    const found = which.stdout.trim().split(/\r?\n/)[0];
    return { kind: "wrapped-cli", command: found, args: [], cwd, source: "legacy(PATH)" };
  }
  // 4. Legacy: in-repo venv.
  const repoRoot = findRepoRoot(cwd);
  const venvPython = join(repoRoot, ".venv", "bin", "python");
  if (existsSync(venvPython)) {
    return {
      kind: "python-module",
      command: venvPython,
      args: ["-m", "animica.cli.main"],
      cwd: repoRoot,
      source: `legacy(${venvPython} -m animica.cli.main)`,
    };
  }
  // 5. Last resort.
  return {
    kind: "python-module",
    command: "python3",
    args: ["-m", "animica.cli.main"],
    cwd: repoRoot,
    source: "legacy(python3 -m animica.cli.main)",
  };
}

export interface RunOptions {
  args: string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv;
  inherit?: boolean;
  detached?: boolean;
  /** When true, capture stdout/stderr instead of inheriting them. */
  capture?: boolean;
  /** Optional input piped to stdin. */
  stdin?: string;
}

export interface RunResult {
  status: number | null;
  stdout: string;
  stderr: string;
  backend: Backend;
}

export function runBackend(opts: RunOptions): RunResult {
  const backend = resolveBackend({ cwd: opts.cwd });
  const args = [...backend.args, ...opts.args];
  const env = { ...process.env, ...(opts.env ?? {}) };
  const spawnOpts: SpawnOptions = {
    cwd: opts.cwd ?? backend.cwd,
    env,
    stdio: opts.capture ? ["pipe", "pipe", "pipe"] : opts.inherit ?? true ? "inherit" : "pipe",
  };
  if (opts.detached) spawnOpts.detached = true;
  const r = spawnSync(backend.command, args, spawnOpts);
  return {
    status: r.status,
    stdout: opts.capture ? (r.stdout?.toString?.() ?? "") : "",
    stderr: opts.capture ? (r.stderr?.toString?.() ?? "") : "",
    backend,
  };
}

export function spawnBackend(opts: RunOptions): ChildProcess {
  const backend = resolveBackend({ cwd: opts.cwd });
  const args = [...backend.args, ...opts.args];
  const env = { ...process.env, ...(opts.env ?? {}) };
  const stdio: SpawnOptions["stdio"] = opts.detached
    ? ["ignore", "ignore", "ignore"]
    : opts.capture
      ? ["pipe", "pipe", "pipe"]
      : "inherit";
  const child = spawn(backend.command, args, {
    cwd: opts.cwd ?? backend.cwd,
    env,
    stdio,
    detached: opts.detached === true,
  });
  return child;
}
