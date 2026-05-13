import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { loadConfig } from "@animica/agent-core";

import { stringFlag } from "../args.js";
import { c, fail, info, ok } from "../output.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
// dist/commands/scaffold.js -> ../../templates
const TEMPLATES_ROOT = join(__dirname, "..", "..", "templates");

interface ScaffoldVars {
  NAME: string;
  SYMBOL?: string;
}

function substitute(text: string, vars: ScaffoldVars): string {
  let out = text.replace(/__NAME__/g, vars.NAME);
  if (vars.SYMBOL) out = out.replace(/__SYMBOL__/g, vars.SYMBOL);
  return out;
}

function copyTemplate(srcDir: string, outDir: string, vars: ScaffoldVars, force: boolean): string[] {
  const written: string[] = [];
  const stack: string[] = [srcDir];
  while (stack.length) {
    const cur = stack.pop()!;
    for (const entry of readdirSync(cur, { withFileTypes: true })) {
      const src = join(cur, entry.name);
      const rel = relative(srcDir, src);
      const dst = join(outDir, substitute(rel, vars));
      if (entry.isDirectory()) {
        stack.push(src);
        mkdirSync(dst, { recursive: true });
        continue;
      }
      if (!entry.isFile()) continue;
      if (existsSync(dst) && !force) {
        info(c.dim(`skip (exists): ${relative(process.cwd(), dst)}`));
        continue;
      }
      const text = readFileSync(src, "utf8");
      mkdirSync(dirname(dst), { recursive: true });
      writeFileSync(dst, substitute(text, vars), "utf8");
      written.push(dst);
    }
  }
  return written;
}

export function runScaffold(kind: "contract" | "dapp" | "token" | "aicf-agent", positionals: string[], options: Record<string, string | boolean>): number {
  const { paths } = loadConfig();
  const name = positionals[0] ?? stringFlag(options, "name");
  if (!name) {
    fail(`usage: animica-agent ${kind} scaffold <name>`);
    return 64;
  }
  const symbol = stringFlag(options, "symbol") ?? name.slice(0, 4).toUpperCase();
  const out = stringFlag(options, "out") ?? join(paths.projectRoot, name);
  if (existsSync(out) && !statSync(out).isDirectory()) {
    fail(`output exists and is not a directory: ${out}`);
    return 1;
  }
  mkdirSync(out, { recursive: true });
  const force = options.force === true;
  const written = copyTemplate(join(TEMPLATES_ROOT, kind), out, { NAME: name, SYMBOL: symbol }, force);
  ok(`scaffolded ${kind} '${name}' at ${relative(process.cwd(), out) || out}`);
  for (const w of written) info(c.dim(`  + ${relative(process.cwd(), w)}`));
  return 0;
}
