/**
 * Billing engine.
 *
 * Combines:
 *   - a pricing table (per-action and per-token rates)
 *   - per-session, daily and monthly budgets
 *   - an allowance grant ledger (user pre-approves a spending cap)
 *   - signed receipts (HMAC over canonical payload; the same hash is used as
 *     a settlement-receipt-id; later phases can lift this to a payment-channel
 *     contract)
 *
 * Settlement is pluggable. The default `OfflineSettlement` records a paid
 * receipt without touching the chain. A `NodeSettlement` is provided that
 * routes through a `Signer` so the user can opt into on-chain settlement
 * once their wallet stack is online.
 *
 * Money is bigint (smallest unit, 18 decimals) end-to-end. No floats.
 */

import { createHash, createHmac, randomUUID } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import type { AgentConfig } from "./config.js";
import { AgentError } from "./errors.js";
import { safeParse, safeStringify } from "./safe-json.js";
import { formatANM, type Signer } from "./wallet.js";

export type ActionKind =
  | "chat-turn"
  | "code-task"
  | "patch-apply"
  | "rpc-call"
  | "scaffold"
  | "doctor"
  | "status";

export interface PricingTier {
  /** Fixed per-action fee in smallest unit. */
  perAction: bigint;
  /** Per 1k input tokens. */
  perKInputTokens: bigint;
  /** Per 1k output tokens. */
  perKOutputTokens: bigint;
  /** Description shown by `animica-agent pricing`. */
  label: string;
}

export interface PricingTable {
  base: PricingTier;
  premium: PricingTier;
  /** Multiplier applied for miner-subsidized users (0..1). */
  minerSubsidy: number;
}

export const DEFAULT_PRICING: PricingTable = {
  base: {
    label: "base — offline / scaffolding / repo-aware ops",
    // 0.0001 ANM per action
    perAction: 100_000_000_000_000n,
    perKInputTokens: 0n,
    perKOutputTokens: 0n,
  },
  premium: {
    label: "premium — remote provider with token-metered usage",
    perAction: 500_000_000_000_000n, // 0.0005 ANM/action
    perKInputTokens: 200_000_000_000_000n, // 0.0002 ANM / 1k input tokens
    perKOutputTokens: 1_000_000_000_000_000n, // 0.001 ANM / 1k output tokens
  },
  minerSubsidy: 0.5, // 50% off for actively mining users
};

export interface EstimateInput {
  kind: ActionKind;
  inputTokens?: number;
  outputTokens?: number;
  /** When true, prefer the premium tier (e.g., remote provider). */
  premium?: boolean;
  /** When true, apply the miner subsidy. */
  minerSubsidized?: boolean;
}

export interface CostEstimate {
  raw: bigint;
  formattedANM: string;
  tier: "base" | "premium";
  breakdown: { component: string; raw: bigint }[];
}

export function estimate(input: EstimateInput, pricing = DEFAULT_PRICING): CostEstimate {
  const tier = input.premium ? "premium" : "base";
  const t = tier === "premium" ? pricing.premium : pricing.base;
  const inK = BigInt(Math.max(0, Math.floor((input.inputTokens ?? 0) / 1000)));
  const outK = BigInt(Math.max(0, Math.floor((input.outputTokens ?? 0) / 1000)));
  const inCost = t.perKInputTokens * inK;
  const outCost = t.perKOutputTokens * outK;
  let raw = t.perAction + inCost + outCost;
  const breakdown = [
    { component: `perAction[${tier}]`, raw: t.perAction },
    { component: `inputTokens(${inK}k)`, raw: inCost },
    { component: `outputTokens(${outK}k)`, raw: outCost },
  ];
  if (input.minerSubsidized) {
    const subsidyBps = Math.max(0, Math.min(10000, Math.round(pricing.minerSubsidy * 10000)));
    const before = raw;
    raw = (before * BigInt(10000 - subsidyBps)) / 10000n;
    breakdown.push({ component: `minerSubsidy(${subsidyBps}bps)`, raw: raw - before });
  }
  return { raw, formattedANM: formatANM(raw), tier, breakdown };
}

/* ---------------- Budgets ---------------- */

export interface BudgetConfig {
  sessionMaxRaw: bigint;
  dailyMaxRaw: bigint;
  monthlyMaxRaw: bigint;
  /** Default ON: prompt before any single action exceeds 1 ANM equivalent. */
  confirmExpensiveAction: boolean;
}

export const DEFAULT_BUDGET: BudgetConfig = {
  sessionMaxRaw: 1_000_000_000_000_000_000n, // 1 ANM
  dailyMaxRaw: 25_000_000_000_000_000_000n, // 25 ANM
  monthlyMaxRaw: 300_000_000_000_000_000_000n, // 300 ANM
  confirmExpensiveAction: true,
};

export interface BudgetState {
  config: BudgetConfig;
  sessionSpentRaw: bigint;
  dailySpentRaw: bigint;
  dailyWindow: string; // YYYY-MM-DD
  monthlySpentRaw: bigint;
  monthlyWindow: string; // YYYY-MM
}

export function rolloverBudget(state: BudgetState, nowISO = new Date().toISOString()): BudgetState {
  const day = nowISO.slice(0, 10);
  const month = nowISO.slice(0, 7);
  if (state.dailyWindow !== day) {
    state = { ...state, dailySpentRaw: 0n, dailyWindow: day };
  }
  if (state.monthlyWindow !== month) {
    state = { ...state, monthlySpentRaw: 0n, monthlyWindow: month };
  }
  return state;
}

export function checkBudget(state: BudgetState, estCost: bigint): { ok: boolean; reason?: string } {
  if (state.sessionSpentRaw + estCost > state.config.sessionMaxRaw) {
    return { ok: false, reason: "session budget exceeded" };
  }
  if (state.dailySpentRaw + estCost > state.config.dailyMaxRaw) {
    return { ok: false, reason: "daily budget exceeded" };
  }
  if (state.monthlySpentRaw + estCost > state.config.monthlyMaxRaw) {
    return { ok: false, reason: "monthly budget exceeded" };
  }
  return { ok: true };
}

/* ---------------- Allowance ---------------- */

export interface AllowanceGrant {
  id: string;
  wallet: string;
  capRaw: bigint;
  consumedRaw: bigint;
  expiresAt: string;
  revokedAt?: string;
  perSessionCapRaw?: bigint;
  perTaskCapRaw?: bigint;
  createdAt: string;
}

/* ---------------- Receipts ---------------- */

export interface ReceiptRequest {
  kind: ActionKind;
  estimate: CostEstimate;
  actualCostRaw?: bigint;
  sessionId?: string;
  projectId?: string;
  wallet?: string;
  worker?: string;
  inputTokens?: number;
  outputTokens?: number;
  toolsUsed?: string[];
  elapsedMs?: number;
  status: "estimated" | "settled" | "refunded" | "failed" | "pending" | "rejected";
  txHash?: string;
  /**
   * Idempotency key. If supplied and a receipt with the same key already
   * exists, charge() returns the existing receipt rather than creating a
   * duplicate. Use `${kind}:${sessionId}:${seq}` or your own scheme.
   */
  idempotencyKey?: string;
}

export interface Receipt extends ReceiptRequest {
  id: string;
  at: string;
  receiptHash: string;
  signature?: string;
}

/* ---------------- Settlement ---------------- */

export interface SettlementResult {
  status: "settled" | "failed";
  txHash?: string;
  signature?: string;
  reason?: string;
}

export interface SettlementBackend {
  readonly name: string;
  settle(receipt: Receipt): Promise<SettlementResult>;
}

export class OfflineSettlement implements SettlementBackend {
  public readonly name = "offline";
  constructor(private readonly secret: string = "animica-agent-local") {}
  async settle(receipt: Receipt): Promise<SettlementResult> {
    const sig = createHmac("sha256", this.secret).update(receipt.receiptHash).digest("hex");
    return { status: "settled", signature: sig };
  }
}

export class NodeSettlement implements SettlementBackend {
  public readonly name = "node";
  constructor(private readonly signer: Signer) {}
  async settle(receipt: Receipt): Promise<SettlementResult> {
    try {
      const res = await this.signer.sign({
        payload: { kind: "agent-receipt", data: { receiptId: receipt.id, hash: receipt.receiptHash, raw: receipt.actualCostRaw ?? receipt.estimate.raw } },
        reason: `Settle agent receipt ${receipt.id} for ${formatANM(receipt.actualCostRaw ?? receipt.estimate.raw)} ANM`,
        estimatedCostRaw: receipt.estimate.raw,
      });
      return { status: "settled", signature: res.signature, txHash: res.txHash };
    } catch (err) {
      return { status: "failed", reason: (err as Error).message };
    }
  }
}

/* ---------------- Engine ---------------- */

export class BillingEngine {
  private allowances: AllowanceGrant[] = [];
  private state: BudgetState;
  private readonly storageDir: string;

  constructor(
    storageDir: string,
    private readonly cfg: AgentConfig,
    private readonly pricing: PricingTable = DEFAULT_PRICING,
    private readonly settlement: SettlementBackend = new OfflineSettlement(),
  ) {
    this.storageDir = storageDir;
    mkdirSync(this.storageDir, { recursive: true });
    this.state = this.loadOrInitState();
    this.allowances = this.loadAllowances();
  }

  /* ---- persistence ---- */

  private get budgetFile(): string {
    return join(this.storageDir, "budget.json");
  }
  private get allowanceFile(): string {
    return join(this.storageDir, "allowances.json");
  }
  private get receiptsFile(): string {
    return join(this.storageDir, "receipts.jsonl");
  }

  private loadOrInitState(): BudgetState {
    if (existsSync(this.budgetFile)) {
      try {
        const j = safeParse<BudgetState>(readFileSync(this.budgetFile, "utf8"));
        return rolloverBudget({
          ...j,
          config: { ...DEFAULT_BUDGET, ...j.config },
        });
      } catch {
        /* fall through */
      }
    }
    return rolloverBudget({
      config: { ...DEFAULT_BUDGET },
      sessionSpentRaw: 0n,
      dailySpentRaw: 0n,
      dailyWindow: new Date().toISOString().slice(0, 10),
      monthlySpentRaw: 0n,
      monthlyWindow: new Date().toISOString().slice(0, 7),
    });
  }

  private saveState(): void {
    writeFileSync(this.budgetFile, safeStringify(this.state, { indent: 2 }) + "\n", "utf8");
  }

  private loadAllowances(): AllowanceGrant[] {
    if (!existsSync(this.allowanceFile)) return [];
    try {
      return safeParse<AllowanceGrant[]>(readFileSync(this.allowanceFile, "utf8"));
    } catch {
      return [];
    }
  }

  private saveAllowances(): void {
    writeFileSync(this.allowanceFile, safeStringify(this.allowances, { indent: 2 }) + "\n", "utf8");
  }

  /* ---- pricing / estimate ---- */

  estimate(input: EstimateInput): CostEstimate {
    return estimate(input, this.pricing);
  }

  getPricing(): PricingTable {
    return this.pricing;
  }

  /* ---- budget ---- */

  setBudget(patch: Partial<BudgetConfig>): BudgetConfig {
    this.state = { ...this.state, config: { ...this.state.config, ...patch } };
    this.saveState();
    return this.state.config;
  }

  getBudget(): BudgetState {
    this.state = rolloverBudget(this.state);
    return this.state;
  }

  /* ---- allowance ---- */

  grant(grant: Omit<AllowanceGrant, "id" | "consumedRaw" | "createdAt">): AllowanceGrant {
    const g: AllowanceGrant = {
      ...grant,
      id: randomUUID(),
      consumedRaw: 0n,
      createdAt: new Date().toISOString(),
    };
    this.allowances.push(g);
    this.saveAllowances();
    return g;
  }

  revoke(id: string): boolean {
    const g = this.allowances.find((a) => a.id === id);
    if (!g) return false;
    g.revokedAt = new Date().toISOString();
    this.saveAllowances();
    return true;
  }

  listAllowances(): AllowanceGrant[] {
    return [...this.allowances];
  }

  activeAllowance(wallet: string): AllowanceGrant | null {
    const now = new Date().toISOString();
    for (const g of this.allowances) {
      if (g.wallet !== wallet) continue;
      if (g.revokedAt) continue;
      if (g.expiresAt && g.expiresAt < now) continue;
      if (g.consumedRaw >= g.capRaw) continue;
      return g;
    }
    return null;
  }

  /* ---- authorize + settle ---- */

  /** Throws AgentError if the action cannot be authorized; otherwise returns the estimate. */
  authorize(input: EstimateInput, wallet?: string): CostEstimate {
    this.state = rolloverBudget(this.state);
    const est = this.estimate(input);
    const budget = checkBudget(this.state, est.raw);
    if (!budget.ok) {
      throw new AgentError(
        "BUDGET",
        `cannot authorize ${input.kind}: ${budget.reason} (estimate=${est.formattedANM} ANM)`,
      );
    }
    if (this.cfg.creditsMode !== "off" && wallet) {
      const allow = this.activeAllowance(wallet);
      if (!allow) {
        throw new AgentError(
          "ALLOWANCE",
          `no active allowance for ${wallet}; grant one with \`animica-agent allowance grant\``,
        );
      }
      if (allow.consumedRaw + est.raw > allow.capRaw) {
        throw new AgentError("ALLOWANCE", `allowance ${allow.id} would be exceeded`);
      }
      if (allow.perTaskCapRaw !== undefined && est.raw > allow.perTaskCapRaw) {
        throw new AgentError("ALLOWANCE", `per-task cap exceeded for allowance ${allow.id}`);
      }
    }
    return est;
  }

  async charge(request: ReceiptRequest): Promise<Receipt> {
    if (request.idempotencyKey) {
      const existing = this.findReceiptByIdempotencyKey(request.idempotencyKey);
      if (existing) return existing;
    }
    const id = randomUUID();
    const at = new Date().toISOString();
    const base = {
      ...request,
      id,
      at,
      actualCostRaw: request.actualCostRaw ?? request.estimate.raw,
    };
    const receiptHash = createHash("sha256")
      .update(safeStringify({ ...base, signature: undefined }))
      .digest("hex");
    let receipt: Receipt = { ...base, receiptHash };
    const settled = await this.settlement.settle(receipt);
    receipt = {
      ...receipt,
      status: settled.status,
      signature: settled.signature,
      txHash: settled.txHash ?? request.txHash,
    };
    appendFileSync(this.receiptsFile, safeStringify(receipt) + "\n", "utf8");

    if (settled.status === "settled") {
      const cost = receipt.actualCostRaw ?? receipt.estimate.raw;
      this.state = {
        ...this.state,
        sessionSpentRaw: this.state.sessionSpentRaw + cost,
        dailySpentRaw: this.state.dailySpentRaw + cost,
        monthlySpentRaw: this.state.monthlySpentRaw + cost,
      };
      this.saveState();
      if (request.wallet) {
        const allow = this.activeAllowance(request.wallet);
        if (allow) {
          allow.consumedRaw += cost;
          this.saveAllowances();
        }
      }
    }
    return receipt;
  }

  listReceipts(limit = 50): Receipt[] {
    if (!existsSync(this.receiptsFile)) return [];
    const all = readFileSync(this.receiptsFile, "utf8").split(/\r?\n/).filter(Boolean);
    const out: Receipt[] = [];
    for (const l of all.slice(-limit)) {
      try {
        out.push(safeParse<Receipt>(l));
      } catch {
        /* skip */
      }
    }
    return out;
  }

  private findReceiptByIdempotencyKey(key: string): Receipt | null {
    if (!existsSync(this.receiptsFile)) return null;
    const all = readFileSync(this.receiptsFile, "utf8").split(/\r?\n/).filter(Boolean);
    // Scan in reverse so the most recent settled receipt wins on duplicates.
    for (let i = all.length - 1; i >= 0; i--) {
      try {
        const r = safeParse<Receipt>(all[i]);
        if (r.idempotencyKey === key) return r;
      } catch {
        /* skip */
      }
    }
    return null;
  }

  exportReceipts(): string {
    if (!existsSync(this.receiptsFile)) return "";
    return readFileSync(this.receiptsFile, "utf8");
  }

  resetSession(): void {
    this.state = { ...this.state, sessionSpentRaw: 0n };
    this.saveState();
  }
}
