/**
 * Persisted agent session + audit log.
 *
 * The session is a small JSON document. Each turn is appended to a JSONL
 * file. We keep the on-disk format BigInt-safe so loaders can be polyglot.
 */

import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

import { safeParse, safeStringify } from "./safe-json.js";

export type SessionRole = "system" | "user" | "assistant" | "tool";

export interface SessionTurn {
  id: string;
  role: SessionRole;
  content: string;
  at: string;
}

export interface AgentSession {
  id: string;
  createdAt: string;
  updatedAt: string;
  /** Optional miner/wallet attribution. */
  attribution?: {
    minerAddress?: string;
    walletAddress?: string;
    worker?: string;
  };
  /** Current conversation. */
  turns: SessionTurn[];
}

export interface AuditEntry {
  id: string;
  at: string;
  kind: string;
  detail?: unknown;
}

export class SessionStore {
  constructor(public readonly stateDir: string) {
    mkdirSync(stateDir, { recursive: true });
  }

  private sessionFile(id: string): string {
    return join(this.stateDir, `session.${id}.json`);
  }

  private get auditFile(): string {
    return join(this.stateDir, "audit.jsonl");
  }

  newSession(attribution?: AgentSession["attribution"]): AgentSession {
    const id = randomUUID();
    const now = new Date().toISOString();
    const s: AgentSession = { id, createdAt: now, updatedAt: now, attribution, turns: [] };
    this.save(s);
    return s;
  }

  load(id: string): AgentSession | null {
    const f = this.sessionFile(id);
    if (!existsSync(f)) return null;
    try {
      return safeParse<AgentSession>(readFileSync(f, "utf8"));
    } catch {
      return null;
    }
  }

  save(session: AgentSession): void {
    session.updatedAt = new Date().toISOString();
    writeFileSync(this.sessionFile(session.id), safeStringify(session, { indent: 2 }) + "\n", "utf8");
  }

  append(session: AgentSession, role: SessionRole, content: string): SessionTurn {
    const turn: SessionTurn = {
      id: randomUUID(),
      role,
      content,
      at: new Date().toISOString(),
    };
    session.turns.push(turn);
    this.save(session);
    this.audit("turn", { sessionId: session.id, turnId: turn.id, role });
    return turn;
  }

  audit(kind: string, detail?: unknown): AuditEntry {
    const entry: AuditEntry = { id: randomUUID(), at: new Date().toISOString(), kind, detail };
    try {
      appendFileSync(this.auditFile, safeStringify(entry) + "\n", "utf8");
    } catch {
      /* audit must not crash callers */
    }
    return entry;
  }

  recentAudit(limit = 50): AuditEntry[] {
    if (!existsSync(this.auditFile)) return [];
    const text = readFileSync(this.auditFile, "utf8");
    const lines = text.split(/\r?\n/).filter(Boolean).slice(-limit);
    const out: AuditEntry[] = [];
    for (const l of lines) {
      try {
        out.push(safeParse<AuditEntry>(l));
      } catch {
        /* skip corrupt lines */
      }
    }
    return out;
  }
}
