import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { existsSync } from "node:fs";

import { loadConfig } from "@animica/agent-core";

import { boolFlag } from "../args.js";
import { c, fail, header, info, kv, ok } from "../output.js";

export function runRelease(options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const cliRoot = findCliRoot();
  header("Release readiness");
  kv([
    ["projectRoot", paths.projectRoot],
    ["cliRoot", cliRoot ?? "(not detected)"],
  ]);
  if (!cliRoot) {
    fail("could not detect animica-agent package root. Run `npm pack --dry-run` from the cli package directly.");
    return 1;
  }
  if (!boolFlag(options, "skip-build", false)) {
    info(c.dim("npm run build"));
    const r = spawnSync("npm", ["run", "build"], { cwd: cliRoot, stdio: "inherit" });
    if (r.status !== 0) {
      fail("build failed");
      return r.status ?? 1;
    }
  }
  if (!boolFlag(options, "skip-test", false)) {
    info(c.dim("npm test"));
    const r = spawnSync("npm", ["test", "--", "--run"], { cwd: cliRoot, stdio: "inherit" });
    if (r.status !== 0) {
      fail("tests failed");
      return r.status ?? 1;
    }
  }
  info(c.dim("npm pack --dry-run"));
  const r = spawnSync("npm", ["pack", "--dry-run"], { cwd: cliRoot, stdio: "inherit" });
  if (r.status !== 0) {
    fail("pack failed");
    return r.status ?? 1;
  }
  ok("release artifact validated. To publish: `cd " + cliRoot + " && npm publish`");
  return 0;
}

function findCliRoot(): string | null {
  // Walk up from import.meta to find a package.json that contains bin/animica-agent.
  let dir = process.cwd();
  for (let i = 0; i < 8; i++) {
    const pkg = join(dir, "package.json");
    if (existsSync(pkg)) {
      try {
        const j = require("node:fs").readFileSync(pkg, "utf8");
        if (j.includes('"animica-agent"')) return dir;
      } catch {
        /* fall through */
      }
    }
    const parent = require("node:path").dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}
