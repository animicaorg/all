/**
 * Browser-side client for the academy backend (api.academy.animica.org).
 *
 * The backend records progress, verifies signed attestations, and dispatches
 * payouts from the reward pool. All payout-affecting endpoints require a
 * fresh signed attestation — the client never holds payout authority on its
 * own, so a compromised CDN cannot drain the pool.
 */

declare const __ACADEMY_API__: string;

const BASE = typeof __ACADEMY_API__ !== "undefined" ? __ACADEMY_API__ : "/api";

async function http<T>(
  method: "GET" | "POST",
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    let msg = text || `${method} ${path} → ${res.status}`;
    try {
      const json = JSON.parse(text);
      if (json?.error) msg = json.error;
      if (json?.message) msg = json.message;
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export interface TutorialSummary {
  id: string;
  title: string;
  description: string;
  difficulty: "intro" | "intermediate" | "advanced";
  rewardAnim: string;
  estimatedMinutes: number;
  steps: number;
}

export interface ProgressEntry {
  tutorialId: string;
  stepId: string;
  completedAt: string;
}

export interface AchievementBadge {
  id: string;
  title: string;
  description: string;
  awardedAt: string | null;
  rewardAnim: string;
}

export interface RewardPoolStatus {
  poolAddress: string;
  balanceBaseUnits: string;
  paidOutBaseUnits: string;
  pendingClaimsBaseUnits: string;
  contributors: number;
}

export interface ClaimRequest {
  tutorialId: string;
  stepId: string;
  address: string;
  nonce: string;
  signature: string;
  message: string;
}

export interface ClaimResult {
  paid: boolean;
  txHash: string | null;
  amountBaseUnits: string;
  reason?: string;
}

export const academyApi = {
  /** Public catalog. Cached on the CDN — safe to call on every page. */
  listTutorials: () => http<TutorialSummary[]>("GET", "/tutorials"),

  /** Single tutorial with full step list (server-side source of truth). */
  getTutorial: (id: string) =>
    http<{
      summary: TutorialSummary;
      steps: Array<{ id: string; title: string; rewardAnim: string }>;
    }>("GET", `/tutorials/${encodeURIComponent(id)}`),

  /** Per-wallet progress. Returns empty list when the address is unknown. */
  getProgress: (address: string) =>
    http<{ progress: ProgressEntry[] }>(
      "GET",
      `/progress/${encodeURIComponent(address)}`,
    ),

  /** Per-wallet badges. Awarded server-side once all-steps criteria match. */
  getAchievements: (address: string) =>
    http<{ badges: AchievementBadge[] }>(
      "GET",
      `/achievements/${encodeURIComponent(address)}`,
    ),

  /**
   * Issues a one-shot nonce the wallet signs alongside the step ID. The
   * server stores the nonce keyed on (address, step) so a single signed
   * attestation can only be redeemed once.
   */
  startClaim: (req: { tutorialId: string; stepId: string; address: string }) =>
    http<{ nonce: string; rewardAnim: string }>("POST", "/claim/start", req),

  /**
   * Verifies the signed attestation and (if payout is enabled and pool has
   * funds) submits an on-chain transfer to the user's address. Returns
   * paid=false with a reason when the claim is duplicate, expired, or the
   * pool is empty.
   */
  finalizeClaim: (req: ClaimRequest) =>
    http<ClaimResult>("POST", "/claim/finalize", req),

  /** Public reward-pool status (balance, paid-out, contributor count). */
  getPoolStatus: () => http<RewardPoolStatus>("GET", "/pool/status"),

  /**
   * Records a successful deposit. The deposit itself is sent directly by
   * the wallet extension — this endpoint just records the txHash so the
   * leaderboard reflects the contribution promptly (the chain indexer also
   * reconciles asynchronously).
   */
  recordDeposit: (req: { address: string; txHash: string; amountAnim: string }) =>
    http<{ recorded: boolean }>("POST", "/pool/deposit", req),
};
