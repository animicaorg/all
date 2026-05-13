/**
 * Patch engine.
 *
 * The agent describes file changes as a `Patch` (a list of `FileOp`). We do
 * not implement a full unified-diff parser here; instead the model is
 * expected to emit structured operations (create / replace / delete / edit).
 * For `edit`, we accept an array of `Hunk` objects with explicit old/new
 * blocks anchored by location. This is more reliable than free-form diffs
 * and is trivial to apply, revert, and golden-test.
 *
 * Every applied patch is journaled to disk so `animica-agent rollback` can
 * restore the previous file contents byte-for-byte.
 */

import { existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import { PatchError } from "./errors.js";
import { Repo } from "./repo.js";
import { safeParse, safeStringify } from "./safe-json.js";

export type FileOp =
  | { kind: "create"; path: string; contents: string }
  | { kind: "replace"; path: string; contents: string }
  | { kind: "delete"; path: string }
  | { kind: "edit"; path: string; hunks: Hunk[] };

export interface Hunk {
  /** Lines that must match the current file contents exactly. */
  oldLines: string[];
  /** Replacement lines. */
  newLines: string[];
  /** 1-based line where oldLines is expected to begin (best-effort anchor). */
  anchorLine?: number;
}

export interface Patch {
  id: string;
  message: string;
  ops: FileOp[];
  createdAt: string;
}

export interface AppliedPatch extends Patch {
  appliedAt: string;
  /** Snapshot of file contents prior to apply so we can rollback. */
  before: { path: string; existed: boolean; contents?: string }[];
}

export interface PatchJournalEntry extends AppliedPatch {
  /** Where this entry is stored. */
  storagePath: string;
}

export function newPatchId(): string {
  const ts = new Date().toISOString().replace(/[^0-9]/g, "").slice(0, 14);
  const rnd = Math.floor(Math.random() * 1e6)
    .toString(36)
    .padStart(4, "0");
  return `p_${ts}_${rnd}`;
}

export function createPatch(message: string, ops: FileOp[]): Patch {
  return { id: newPatchId(), message, ops, createdAt: new Date().toISOString() };
}

/** Render a human-friendly text preview of a patch (creation diff for new files, +/- for edits). */
export function renderPatchPreview(patch: Patch, repo: Repo): string {
  const lines: string[] = [];
  lines.push(`# patch ${patch.id}`);
  lines.push(`# message: ${patch.message}`);
  for (const op of patch.ops) {
    lines.push("");
    switch (op.kind) {
      case "create": {
        lines.push(`+++ create ${op.path}`);
        for (const l of op.contents.split(/\r?\n/)) lines.push(`+ ${l}`);
        break;
      }
      case "replace": {
        const had = repo.exists(op.path);
        lines.push(had ? `~~~ replace ${op.path}` : `+++ create ${op.path}`);
        for (const l of op.contents.split(/\r?\n/)) lines.push(`+ ${l}`);
        break;
      }
      case "delete": {
        lines.push(`--- delete ${op.path}`);
        if (repo.exists(op.path)) {
          for (const l of repo.read(op.path).split(/\r?\n/)) lines.push(`- ${l}`);
        }
        break;
      }
      case "edit": {
        lines.push(`@@ edit ${op.path}`);
        for (const h of op.hunks) {
          const anchor = h.anchorLine ? `@ ${h.anchorLine}` : "";
          lines.push(`@@${anchor}`);
          for (const l of h.oldLines) lines.push(`- ${l}`);
          for (const l of h.newLines) lines.push(`+ ${l}`);
        }
        break;
      }
    }
  }
  return lines.join("\n");
}

function snapshot(repo: Repo, path: string): { path: string; existed: boolean; contents?: string } {
  if (!repo.exists(path)) return { path, existed: false };
  return { path, existed: true, contents: repo.read(path) };
}

function applyEdit(original: string, hunks: Hunk[]): string {
  let text = original;
  for (const h of hunks) {
    const oldBlock = h.oldLines.join("\n");
    if (oldBlock.length === 0) {
      // append-only hunk: stitch new lines at the anchor or end.
      const insert = h.newLines.join("\n");
      if (h.anchorLine === undefined) {
        text = text.endsWith("\n") ? text + insert : text + "\n" + insert;
      } else {
        const lines = text.split("\n");
        const idx = Math.max(0, Math.min(lines.length, h.anchorLine - 1));
        lines.splice(idx, 0, ...h.newLines);
        text = lines.join("\n");
      }
      continue;
    }
    const idx = text.indexOf(oldBlock);
    if (idx === -1) {
      throw new PatchError(
        `Hunk did not match: anchor=${h.anchorLine ?? "<none>"}; oldLines[0]=${JSON.stringify(h.oldLines[0] ?? "")}`,
      );
    }
    if (text.indexOf(oldBlock, idx + 1) !== -1) {
      throw new PatchError(
        `Hunk is ambiguous: anchor=${h.anchorLine ?? "<none>"} matches multiple locations`,
      );
    }
    text = text.slice(0, idx) + h.newLines.join("\n") + text.slice(idx + oldBlock.length);
  }
  return text;
}

export interface ApplyOptions {
  repo: Repo;
  patch: Patch;
  dryRun?: boolean;
  journalDir: string;
}

export function applyPatch(opts: ApplyOptions): AppliedPatch {
  const { repo, patch } = opts;
  const before: AppliedPatch["before"] = [];

  // Snapshot first so a mid-apply failure leaves us able to revert.
  for (const op of patch.ops) {
    before.push(snapshot(repo, "path" in op ? op.path : (op as { path: string }).path));
  }

  const plannedWrites: { path: string; contents: string; create?: boolean }[] = [];
  const plannedDeletes: string[] = [];
  for (const op of patch.ops) {
    switch (op.kind) {
      case "create": {
        if (repo.exists(op.path)) {
          throw new PatchError(`create failed, file already exists: ${op.path}`);
        }
        plannedWrites.push({ path: op.path, contents: op.contents, create: true });
        break;
      }
      case "replace": {
        plannedWrites.push({ path: op.path, contents: op.contents });
        break;
      }
      case "delete": {
        if (!repo.exists(op.path)) throw new PatchError(`delete failed, missing file: ${op.path}`);
        plannedDeletes.push(op.path);
        break;
      }
      case "edit": {
        if (!repo.exists(op.path)) throw new PatchError(`edit failed, missing file: ${op.path}`);
        const original = repo.read(op.path);
        const next = applyEdit(original, op.hunks);
        plannedWrites.push({ path: op.path, contents: next });
        break;
      }
    }
  }

  if (opts.dryRun) {
    return { ...patch, appliedAt: new Date().toISOString(), before };
  }

  for (const w of plannedWrites) {
    const abs = repo.safeJoin(w.path);
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, w.contents, "utf8");
  }
  for (const p of plannedDeletes) {
    const abs = repo.safeJoin(p);
    rmSync(abs, { force: true });
  }

  const applied: AppliedPatch = { ...patch, appliedAt: new Date().toISOString(), before };
  writeJournal(opts.journalDir, applied);
  return applied;
}

export function writeJournal(journalDir: string, applied: AppliedPatch): string {
  mkdirSync(journalDir, { recursive: true });
  const file = join(journalDir, `${applied.id}.json`);
  writeFileSync(file, safeStringify(applied, { indent: 2 }) + "\n", "utf8");
  return file;
}

export function listJournal(journalDir: string): PatchJournalEntry[] {
  if (!existsSync(journalDir)) return [];
  const out: PatchJournalEntry[] = [];
  for (const entry of (() => {
    try {
      return require("node:fs").readdirSync(journalDir) as string[];
    } catch {
      return [];
    }
  })()) {
    if (!entry.endsWith(".json")) continue;
    const storagePath = join(journalDir, entry);
    try {
      const j = safeParse<AppliedPatch>(readFileSync(storagePath, "utf8"));
      out.push({ ...j, storagePath });
    } catch {
      continue;
    }
  }
  out.sort((a, b) => (a.appliedAt < b.appliedAt ? 1 : -1));
  return out;
}

export function readLatestJournal(journalDir: string): PatchJournalEntry | null {
  const all = listJournal(journalDir);
  return all[0] ?? null;
}

export function rollbackPatch(repo: Repo, entry: PatchJournalEntry): void {
  for (const snap of entry.before) {
    const abs = repo.safeJoin(snap.path);
    if (snap.existed) {
      mkdirSync(dirname(abs), { recursive: true });
      writeFileSync(abs, snap.contents ?? "", "utf8");
    } else if (existsSync(abs)) {
      rmSync(abs, { force: true });
    }
  }
  rmSync(entry.storagePath, { force: true });
}

/** Heuristic check: warn-loud if any op would touch likely-secret files. */
export function findSensitiveTargets(patch: Patch): string[] {
  const flagged: string[] = [];
  for (const op of patch.ops) {
    const path = (op as { path?: string }).path ?? "";
    const lower = path.toLowerCase();
    if (/(^|\/)(\.env|\.env\.[^/]+|secrets?\.[^/]+|credentials\.[^/]+|.*\.key|.*\.pem)$/.test(lower)) {
      flagged.push(path);
    }
  }
  return flagged;
}

/** Convenience: stat a journal dir for status output. */
export function journalStats(journalDir: string): { count: number; latest?: string } {
  if (!existsSync(journalDir)) return { count: 0 };
  let count = 0;
  let latest: string | undefined;
  try {
    for (const f of require("node:fs").readdirSync(journalDir) as string[]) {
      if (!f.endsWith(".json")) continue;
      count++;
      const full = join(journalDir, f);
      const m = statSync(full).mtimeMs;
      if (!latest || statSync(latest).mtimeMs < m) latest = full;
    }
  } catch {
    /* fall through */
  }
  return { count, latest };
}
