/**
 * Usage accounting hooks.
 *
 * Records every agent job with miner/wallet attribution so AICF or off-chain
 * billing systems can later consume the journal. This module never talks to
 * the network; downstream systems read the JSONL and choose how to settle.
 *
 * Receipts are append-only; rotation/compaction is out of scope here.
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { randomUUID, createHash } from "node:crypto";

import { safeParse, safeStringify } from "./safe-json.js";

export type UsageJobKind =
  | "chat-turn"
  | "code-task"
  | "patch-apply"
  | "patch-rollback"
  | "rpc-call"
  | "scaffold"
  | "doctor"
  | "status";

/** @deprecated alias kept for callers — prefer UsageJobKind. */
export type JobKindUsage = UsageJobKind;

export interface UsageRecord {
  id: string;
  at: string;
  kind: UsageJobKind;
  /** Free-form details, redacted before logging. */
  detail?: Record<string, unknown>;
  attribution: {
    minerAddress?: string;
    walletAddress?: string;
    worker?: string;
    creditsMode?: string;
    aicfMode?: string;
  };
  /** Optional self-hash for offline receipt verification. */
  receipt?: string;
}

export class UsageJournal {
  constructor(private readonly stateDir: string) {
    mkdirSync(stateDir, { recursive: true });
  }

  private get file(): string {
    return join(this.stateDir, "usage.jsonl");
  }

  record(rec: Omit<UsageRecord, "id" | "at" | "receipt">): UsageRecord {
    const id = randomUUID();
    const at = new Date().toISOString();
    const base: UsageRecord = { ...rec, id, at };
    const receipt = createHash("sha256").update(safeStringify(base)).digest("hex");
    const full: UsageRecord = { ...base, receipt };
    appendFileSync(this.file, safeStringify(full) + "\n", "utf8");
    return full;
  }

  recent(limit = 50): UsageRecord[] {
    if (!existsSync(this.file)) return [];
    const lines = readFileSync(this.file, "utf8").split(/\r?\n/).filter(Boolean).slice(-limit);
    const out: UsageRecord[] = [];
    for (const l of lines) {
      try {
        out.push(safeParse<UsageRecord>(l));
      } catch {
        /* skip */
      }
    }
    return out;
  }

  summarize(): { total: number; byKind: Record<string, number>; firstAt?: string; lastAt?: string } {
    if (!existsSync(this.file)) return { total: 0, byKind: {} };
    let total = 0;
    const byKind: Record<string, number> = {};
    let firstAt: string | undefined;
    let lastAt: string | undefined;
    for (const l of readFileSync(this.file, "utf8").split(/\r?\n/).filter(Boolean)) {
      try {
        const r = safeParse<UsageRecord>(l);
        total++;
        byKind[r.kind] = (byKind[r.kind] ?? 0) + 1;
        if (!firstAt) firstAt = r.at;
        lastAt = r.at;
      } catch {
        /* skip */
      }
    }
    return { total, byKind, firstAt, lastAt };
  }

  /** Reset the journal (operator action). Returns count of records dropped. */
  reset(): number {
    const before = this.summarize().total;
    writeFileSync(this.file, "", "utf8");
    return before;
  }
}
