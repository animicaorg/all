// Agent route helpers: budget validation and the owner-facing serialization.

import { ApiError } from '@/lib/api';
import { dayKeyUtc } from '../apps/_shared';

/**
 * Server-side budget rules (§20): budgets are non-negative integers, and a daily cap without a
 * per-run cap is refused — the runner reserves maxSpendPerRunNanm against the daily cap BEFORE
 * a run starts, which is what makes two concurrent runs unable to blow past the cap. No per-run
 * bound would make that reservation impossible, so the combination is rejected outright.
 */
export function validateAgentBudget(maxSpendPerRunNanm: bigint, dailySpendCapNanm: bigint) {
  if (dailySpendCapNanm > 0n && maxSpendPerRunNanm <= 0n) {
    throw new ApiError(
      400,
      'budget_invalid',
      'a daily spend cap requires a per-run cap (maxSpendPerRunNanm > 0) so runs can be reserved against it',
    );
  }
  if (dailySpendCapNanm > 0n && maxSpendPerRunNanm > dailySpendCapNanm) {
    throw new ApiError(400, 'budget_invalid', 'maxSpendPerRunNanm cannot exceed dailySpendCapNanm');
  }
}

export function agentView(a: {
  id: string;
  slug: string;
  name: string;
  description: string;
  address: string | null;
  status: string;
  capabilities: string[];
  maxSpendPerRunNanm: bigint;
  dailySpendCapNanm: bigint;
  spentTodayNanm: bigint;
  spendDayKey: string;
  lastRunAt: Date | null;
  runsTotal: number;
  createdAt: Date;
  updatedAt: Date;
  function?: { slug: string; name: string; status: string } | null;
  app?: { slug: string; name: string } | null;
}) {
  const spentToday = a.spendDayKey === dayKeyUtc() ? a.spentTodayNanm : 0n;
  return {
    id: a.id,
    slug: a.slug,
    name: a.name,
    description: a.description,
    address: a.address,
    status: a.status,
    capabilities: a.capabilities,
    budget: {
      maxSpendPerRunNanm: a.maxSpendPerRunNanm,
      dailySpendCapNanm: a.dailySpendCapNanm,
      spentTodayNanm: spentToday,
      remainingTodayNanm:
        a.dailySpendCapNanm > 0n ? (a.dailySpendCapNanm > spentToday ? a.dailySpendCapNanm - spentToday : 0n) : null,
    },
    lastRunAt: a.lastRunAt,
    runsTotal: a.runsTotal,
    function: a.function ? { slug: a.function.slug, name: a.function.name, status: a.function.status } : undefined,
    app: a.app ? { slug: a.app.slug, name: a.app.name } : null,
    createdAt: a.createdAt,
    updatedAt: a.updatedAt,
  };
}
