/**
 * Repo adapter: discovery, tree walking, file IO, simple search.
 *
 * Intentionally avoids shelling out to git so it works on a non-git workspace
 * (the agent must support new projects and scaffolded folders). Git-aware
 * operations live in a dedicated git helper file.
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { extname, join, relative, resolve } from "node:path";

import { safeStringify } from "./safe-json.js";

const DEFAULT_IGNORE = new Set([
  ".git",
  "node_modules",
  ".venv",
  "dist",
  "build",
  "target",
  ".pnpm-store",
  ".pytest_cache",
  ".ruff_cache",
  ".coverage",
  "logs",
  "artifacts",
  "__pycache__",
  ".animica",
  ".DS_Store",
]);

const TEXT_EXTS = new Set([
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".mjs",
  ".cjs",
  ".json",
  ".md",
  ".py",
  ".rs",
  ".go",
  ".sh",
  ".yaml",
  ".yml",
  ".toml",
  ".css",
  ".scss",
  ".html",
  ".sol",
  ".vue",
  ".svelte",
]);

export interface FileEntry {
  path: string; // POSIX, relative to repo root
  size: number;
  isText: boolean;
}

export interface RepoSummary {
  root: string;
  totalFiles: number;
  totalBytes: number;
  topDirs: { dir: string; files: number }[];
}

export class Repo {
  constructor(public readonly root: string) {
    if (!existsSync(root)) {
      throw new Error(`Repo root does not exist: ${root}`);
    }
  }

  /** Read a file relative to the repo root. Throws if outside the root. */
  read(path: string): string {
    const abs = this.safeJoin(path);
    return readFileSync(abs, "utf8");
  }

  /** Read a file as a Buffer relative to repo root. */
  readBytes(path: string): Buffer {
    const abs = this.safeJoin(path);
    return readFileSync(abs);
  }

  exists(path: string): boolean {
    try {
      return existsSync(this.safeJoin(path));
    } catch {
      return false;
    }
  }

  /**
   * Walk the repo root yielding text-ish files. ignore is matched against
   * directory or file names anywhere in the path.
   */
  *walk(options: { extraIgnore?: Iterable<string>; includeBinary?: boolean } = {}): Generator<FileEntry> {
    const ignore = new Set(DEFAULT_IGNORE);
    for (const v of options.extraIgnore ?? []) ignore.add(v);
    const stack: string[] = [this.root];
    while (stack.length) {
      const cur = stack.pop()!;
      let entries;
      try {
        entries = readdirSync(cur, { withFileTypes: true });
      } catch {
        continue;
      }
      for (const e of entries) {
        if (ignore.has(e.name)) continue;
        const full = join(cur, e.name);
        if (e.isDirectory()) {
          stack.push(full);
          continue;
        }
        if (!e.isFile()) continue;
        const ext = extname(e.name);
        const isText = TEXT_EXTS.has(ext);
        if (!isText && !options.includeBinary) continue;
        let size = 0;
        try {
          size = statSync(full).size;
        } catch {
          continue;
        }
        yield {
          path: relative(this.root, full).split("\\").join("/"),
          size,
          isText,
        };
      }
    }
  }

  /** Naïve line-level grep over text files. Returns at most `limit` hits. */
  grep(pattern: string | RegExp, options: { limit?: number; flags?: string } = {}): {
    file: string;
    line: number;
    text: string;
  }[] {
    const re = typeof pattern === "string" ? new RegExp(pattern, options.flags ?? "i") : pattern;
    const limit = options.limit ?? 200;
    const out: { file: string; line: number; text: string }[] = [];
    for (const entry of this.walk()) {
      if (!entry.isText) continue;
      let text: string;
      try {
        text = readFileSync(join(this.root, entry.path), "utf8");
      } catch {
        continue;
      }
      const lines = text.split(/\r?\n/);
      for (let i = 0; i < lines.length; i++) {
        if (re.test(lines[i])) {
          out.push({ file: entry.path, line: i + 1, text: lines[i].slice(0, 400) });
          if (out.length >= limit) return out;
        }
      }
    }
    return out;
  }

  summary(): RepoSummary {
    const dirs = new Map<string, number>();
    let files = 0;
    let bytes = 0;
    for (const e of this.walk({ includeBinary: true })) {
      files++;
      bytes += e.size;
      const top = e.path.split("/")[0] ?? "(root)";
      dirs.set(top, (dirs.get(top) ?? 0) + 1);
    }
    const topDirs = [...dirs.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 12)
      .map(([dir, files]) => ({ dir, files }));
    return { root: this.root, totalFiles: files, totalBytes: bytes, topDirs };
  }

  /** Always returns a path strictly under the repo root. Throws on escape. */
  safeJoin(path: string): string {
    const abs = resolve(this.root, path);
    const rel = relative(this.root, abs);
    if (rel.startsWith("..") || resolve(this.root, rel) !== abs) {
      throw new Error(`Path escapes repo root: ${path}`);
    }
    return abs;
  }

  /** Stringify the summary for status output. */
  toJSON(): string {
    return safeStringify(this.summary(), { indent: 2 });
  }
}
