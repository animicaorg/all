/**
 * Backend detection & invocation for animica-node.
 *
 * We orchestrate the **existing** Python Animica node runtime that ships with
 * the monorepo (`python/animica/cli/main.py`). This is real: we never mock,
 * never re-implement, and never modify mining or node behavior — we only
 * spawn the same CLI a developer would run by hand.
 *
 * Resolution order:
 *   1. ANIMICA_NODE_BIN=<path>            (operator override)
 *   2. installed `animica` shell command  (preferred for global installs)
 *   3. <repoRoot>/.venv/bin/python -m animica.cli.main   (devcontainer-style)
 *   4. python3 -m animica.cli.main        (last resort PATH lookup)
 *
 * In all cases the bin is spawned with the user's current environment so
 * existing ANIMICA_* env vars (RPC URL, chain id, miner config) are honored
 * exactly as they would be without this package.
 */

import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { spawn, spawnSync, type ChildProcess, type SpawnOptions } from "node:child_process";
import { findRepoRoot } from "@animica/agent-core";

export interface Backend {
  kind: "wrapped-cli" | "python-module";
  command: string;
  args: string[];
  cwd: string;
  source: string;
}

export interface ResolveOptions {
  cwd?: string;
}

export function resolveBackend(opts: ResolveOptions = {}): Backend {
  const cwd = opts.cwd ?? process.cwd();
  const repoRoot = findRepoRoot(cwd);
  const envBin = process.env.ANIMICA_NODE_BIN;
  if (envBin && existsSync(envBin)) {
    return { kind: "wrapped-cli", command: envBin, args: [], cwd, source: "env(ANIMICA_NODE_BIN)" };
  }
  const which = spawnSync("which", ["animica"], { encoding: "utf8" });
  if (which.status === 0 && which.stdout.trim()) {
    return { kind: "wrapped-cli", command: which.stdout.trim(), args: [], cwd, source: "PATH" };
  }
  const venvPython = join(repoRoot, ".venv", "bin", "python");
  if (existsSync(venvPython)) {
    return {
      kind: "python-module",
      command: venvPython,
      args: ["-m", "animica.cli.main"],
      cwd: repoRoot,
      source: `${venvPython} -m animica.cli.main`,
    };
  }
  // Last resort: hope python3 + animica package is on PATH.
  return {
    kind: "python-module",
    command: "python3",
    args: ["-m", "animica.cli.main"],
    cwd: repoRoot,
    source: "python3 -m animica.cli.main",
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
