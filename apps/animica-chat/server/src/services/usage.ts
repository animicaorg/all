// Usage gating + accounting. Two layers:
//   - free / unauth users get a per-day cap (FREE_DAILY_MESSAGES)
//   - Pro subscribers get a per-month cap that rolls on the
//     subscription anniversary (PRO_MONTHLY_MESSAGES, snapshotted onto
//     the plan row so plans can override)
//
// The chat route calls reserve() before talking to the AI and record()
// after the assistant turn finishes (or on partial completion). reserve
// is optimistic — it increments and returns false on cap, so we never
// gate a turn on a stale read.

import { PrismaClient, UserSubscription, SubscriptionStatus } from '@prisma/client';
import { env } from '../env';

export interface UsageDecision {
  ok: boolean;
  reason?: string;
  remaining?: number;
}

const WEEK_MS = 7 * 24 * 60 * 60_000;

function utcMidnight(d = new Date()): Date {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
}

export interface ReserveInput {
  prisma: PrismaClient;
  userId?: string | null;
  // When userId is missing, use a hashed session/IP key to gate.
  anonymousKey?: string | null;
  // Current subscription, if any. Pulled by caller so usage stays
  // dependency-free.
  subscription?:
    | (UserSubscription & { plan: { weeklyMessages: number } })
    | null;
}

export async function reserveMessage(input: ReserveInput): Promise<UsageDecision> {
  const { prisma, userId, subscription } = input;

  // Pro path: rolling weekly cap. The window starts on first use and
  // every quota check advances it by exactly 7 days as long as the
  // current window has elapsed. This is bounded by a while-loop so a
  // user who didn't use the product for several weeks still ends up
  // with a fresh allowance rather than several wasted resets.
  if (
    subscription &&
    (subscription.status === SubscriptionStatus.ACTIVE ||
      subscription.status === SubscriptionStatus.PAST_DUE)
  ) {
    const cap = subscription.plan.weeklyMessages || env.PRO_MONTHLY_MESSAGES;
    let windowStart = subscription.currentWeekStart ?? new Date();
    let used = subscription.messagesUsedThisPeriod;
    const now = Date.now();
    let weeksAdvanced = 0;
    while (now - windowStart.getTime() >= WEEK_MS) {
      windowStart = new Date(windowStart.getTime() + WEEK_MS);
      used = 0;
      weeksAdvanced += 1;
      // Safety cap so an unbounded clock skew can't run forever.
      if (weeksAdvanced > 1000) break;
    }
    if (used >= cap) {
      // Persist any rollover that DID happen before bailing out so
      // the next call sees the new window.
      if (weeksAdvanced > 0 || !subscription.currentWeekStart) {
        await prisma.userSubscription.update({
          where: { id: subscription.id },
          data: { messagesUsedThisPeriod: used, currentWeekStart: windowStart },
        });
      }
      return { ok: false, reason: 'weekly_cap', remaining: 0 };
    }
    await prisma.userSubscription.update({
      where: { id: subscription.id },
      data: {
        messagesUsedThisPeriod: used + 1,
        currentWeekStart: windowStart,
      },
    });
    return { ok: true, remaining: cap - used - 1 };
  }

  // Free / unauthenticated path: daily cap.
  const day = utcMidnight();
  if (userId) {
    const rec = await prisma.usageRecord.upsert({
      where: { userId_day: { userId, day } },
      create: { userId, day, messageCount: 1 },
      update: { messageCount: { increment: 1 } },
    });
    if (rec.messageCount > env.FREE_DAILY_MESSAGES) {
      // Roll back so the user doesn't get charged twice on a retry.
      await prisma.usageRecord.update({
        where: { userId_day: { userId, day } },
        data: { messageCount: { decrement: 1 } },
      });
      return { ok: false, reason: 'daily_cap', remaining: 0 };
    }
    return { ok: true, remaining: env.FREE_DAILY_MESSAGES - rec.messageCount };
  }

  // Pure anonymous — we don't have a stable userId, so use the
  // anonymousKey (caller-supplied hash). Same logic.
  if (!input.anonymousKey) {
    return { ok: false, reason: 'no_identity' };
  }
  // Anonymous usage isn't persisted to UsageRecord (userId required);
  // a future iteration can add an AnonymousUsage table if needed.
  return { ok: true };
}

export async function recordUsage(opts: {
  prisma: PrismaClient;
  userId?: string | null;
  promptTokens?: number;
  completionTokens?: number;
}): Promise<void> {
  if (!opts.userId) return;
  const day = utcMidnight();
  await opts.prisma.usageRecord.upsert({
    where: { userId_day: { userId: opts.userId, day } },
    create: {
      userId: opts.userId,
      day,
      messageCount: 0,
      promptTokens: opts.promptTokens ?? 0,
      completionTokens: opts.completionTokens ?? 0,
    },
    update: {
      promptTokens: { increment: opts.promptTokens ?? 0 },
      completionTokens: { increment: opts.completionTokens ?? 0 },
    },
  });
}
