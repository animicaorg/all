/**
 * Backend bridge for `animica-agent` wallet/RPC ops.
 *
 * The agent needs to invoke the Animica node backend to create wallets,
 * sign transactions, etc. Historically that was `python3 -m animica.cli.main`,
 * which assumes a repo checkout. With the npm-only installation path this is
 * no longer true — we must prefer the managed runtime that
 * `animica-node install-runtime` provisions under `~/.animica/runtime/`.
 *
 * To keep `animica-agent` independently installable from npm (no hard dep on
 * `animica-node`), this module re-implements just enough lookup to find a
 * managed runtime that `animica-node` would have written. The resolution
 * order matches `animica-node`'s `resolveBackend`:
 *
 *   1. ANIMICA_NODE_BIN              operator override
 *   2. ~/.animica/runtime/current.json + per-install marker (managed runtime)
 *   3. `animica` on PATH             legacy dev install
 *   4. python3 -m animica.cli.main   final fallback (legacy repo)
 *
 * `source` surfaces which path matched, so the agent can tell users
 * whether they need to `animica-node install-runtime`.
 */

import { spawnSync, type SpawnSyncOptions } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export type BackendKind = "binary" | "python-module";
export type BackendSource = "env-override" | "managed" | "path" | "legacy-venv" | "legacy-python";

export interface AgentBackend {
  kind: BackendKind;
  command: string;
  /** Args prepended before any user args (e.g. `["-m", "animica.cli.main"]`). */
  baseArgs: string[];
  source: BackendSource;
  /** Human-readable description for `agent doctor`. */
  description: string;
}

interface ManagedPointer {
  schema: number;
  active?: { channel: string; version: string; platformKey: string };
}

interface ManagedMarker {
  channel: string;
  version: string;
  platformKey: string;
  installDir: string;
  entry: string;
}

function runtimeRoot(): string {
  return process.env.ANIMICA_RUNTIME_HOME ?? join(homedir(), ".animica", "runtime");
}

function readJson<T>(path: string): T | null {
  try {
    return JSON.parse(readFileSync(path, "utf8")) as T;
  } catch {
    return null;
  }
}

function findManagedBackend(): AgentBackend | null {
  const root = runtimeRoot();
  const ptr = readJson<ManagedPointer>(join(root, "current.json"));
  if (!ptr?.active) return null;
  const dir = join(root, "versions", `${ptr.active.channel}-${ptr.active.version}-${ptr.active.platformKey}`);
  const marker = readJson<ManagedMarker>(join(dir, ".animica-runtime.json"));
  if (!marker) return null;
  const cmd = join(dir, marker.entry);
  if (!existsSync(cmd)) return null;
  return {
    kind: "binary",
    command: cmd,
    baseArgs: [],
    source: "managed",
    description: `managed-runtime(${marker.channel}@${marker.version})`,
  };
}

function findPathBackend(): AgentBackend | null {
  const w = spawnSync(process.platform === "win32" ? "where" : "which", ["animica"], {
    encoding: "utf8",
  });
  if (w.status === 0 && w.stdout && w.stdout.trim()) {
    const found = w.stdout.trim().split(/\r?\n/)[0];
    return {
      kind: "binary",
      command: found,
      baseArgs: [],
      source: "path",
      description: `animica (${found})`,
    };
  }
  return null;
}

function legacyPythonBackend(): AgentBackend {
  const python = process.env.ANIMICA_AGENT_PYTHON ?? "python3";
  return {
    kind: "python-module",
    command: python,
    baseArgs: ["-m", "animica.cli.main"],
    source: "legacy-python",
    description: `${python} -m animica.cli.main (legacy)`,
  };
}

/**
 * Resolve the best Animica backend available for invoking wallet/CLI ops.
 *
 * If `opts.requireManaged` is true, returns null when no managed runtime is
 * active (used by setup to refuse silently degrading to legacy paths when
 * the user explicitly asked for the npm-only flow).
 */
export function resolveAgentBackend(opts: { requireManaged?: boolean } = {}): AgentBackend | null {
  const env = process.env.ANIMICA_NODE_BIN;
  if (env && existsSync(env)) {
    return {
      kind: "binary",
      command: env,
      baseArgs: [],
      source: "env-override",
      description: `ANIMICA_NODE_BIN=${env}`,
    };
  }
  const managed = findManagedBackend();
  if (managed) return managed;
  if (opts.requireManaged) return null;
  const onPath = findPathBackend();
  if (onPath) return onPath;
  return legacyPythonBackend();
}

export interface BackendRunOptions extends Pick<SpawnSyncOptions, "cwd" | "input" | "env"> {
  /** Force a specific backend (advanced; bypasses resolution). */
  backend?: AgentBackend;
}

export interface BackendRunResult {
  status: number;
  stdout: string;
  stderr: string;
  backend: AgentBackend;
}

/**
 * Invoke the backend with the given args. The args are forwarded verbatim;
 * the caller is responsible for the subcommand structure (e.g.
 * `["wallet", "new", "--label", "main"]`).
 */
export function runBackendCli(args: string[], opts: BackendRunOptions = {}): BackendRunResult {
  const backend = opts.backend ?? resolveAgentBackend();
  if (!backend) {
    return {
      status: 127,
      stdout: "",
      stderr:
        "no Animica backend available. Run `animica-node install-runtime` or set ANIMICA_NODE_BIN.\n",
      backend: {
        kind: "binary",
        command: "",
        baseArgs: [],
        source: "managed",
        description: "(unresolved)",
      },
    };
  }
  const r = spawnSync(backend.command, [...backend.baseArgs, ...args], {
    cwd: opts.cwd,
    input: opts.input,
    env: opts.env ?? process.env,
    encoding: "utf8",
  });
  return {
    status: r.status ?? 1,
    stdout: r.stdout ?? "",
    stderr: r.stderr ?? "",
    backend,
  };
}

/** True if at least one usable backend is reachable. Excludes legacy-python (which may not actually have the module installed). */
export function hasUsableBackend(): boolean {
  const env = process.env.ANIMICA_NODE_BIN;
  if (env && existsSync(env)) return true;
  if (findManagedBackend()) return true;
  if (findPathBackend()) return true;
  return false;
}

/** True if the legacy `python3 -m animica.cli.main` path is likely to work — best-effort import probe. */
export function legacyPythonImports(): boolean {
  const python = process.env.ANIMICA_AGENT_PYTHON ?? "python3";
  const r = spawnSync(python, ["-c", "import animica.cli.main"], { encoding: "utf8" });
  return r.status === 0;
}
