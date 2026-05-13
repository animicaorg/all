/**
 * Lightweight git helpers. Avoid pulling in a dependency by shelling out to
 * the `git` binary. All helpers return null/empty on failure rather than
 * throwing so status flows never crash on a non-git workspace.
 */

import { spawnSync } from "node:child_process";

function run(args: string[], cwd: string): string | null {
  const r = spawnSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
  if (r.error || r.status !== 0) return null;
  return r.stdout.toString();
}

export interface GitInfo {
  isRepo: boolean;
  branch?: string;
  head?: string;
  dirty?: boolean;
  remote?: string;
  staged?: number;
  unstaged?: number;
  untracked?: number;
}

export function gitInfo(cwd: string): GitInfo {
  const insideRepo = run(["rev-parse", "--is-inside-work-tree"], cwd)?.trim() === "true";
  if (!insideRepo) return { isRepo: false };
  const branch = run(["rev-parse", "--abbrev-ref", "HEAD"], cwd)?.trim();
  const head = run(["rev-parse", "HEAD"], cwd)?.trim();
  const remote = run(["remote", "get-url", "origin"], cwd)?.trim();
  const status = run(["status", "--porcelain=v1"], cwd) ?? "";
  let staged = 0;
  let unstaged = 0;
  let untracked = 0;
  for (const line of status.split(/\r?\n/)) {
    if (!line) continue;
    const xy = line.slice(0, 2);
    if (xy === "??") untracked++;
    else {
      if (xy[0] !== " " && xy[0] !== "?") staged++;
      if (xy[1] !== " ") unstaged++;
    }
  }
  return {
    isRepo: true,
    branch,
    head,
    remote: remote || undefined,
    staged,
    unstaged,
    untracked,
    dirty: staged + unstaged + untracked > 0,
  };
}
