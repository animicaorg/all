import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, authenticate, requireScope, ApiError } from '@/lib/api';
import { invokeFunction } from '@/lib/cloud/executor';
import { enforceBurst } from '@/lib/cloud/ratelimit';
import { limits, flags } from '@/lib/cloud/config';
import { dayKeyUtc } from '../../../apps/_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const maxDuration = 300;

// POST /api/cloud/v1/agents/[slug]/run — run the agent NOW (callerKind: agent).
//
// The autonomy budget is enforced here, atomically, BEFORE any code runs:
//   1. if a daily cap is set, the per-run cap is RESERVED against today's spend in one
//      conditional UPDATE — two concurrent runs cannot both take the last slice;
//   2. the execution itself is invoked with maxSpendNanm = the per-run cap, which the
//      executor enforces across the whole nested call tree;
//   3. afterwards the reservation is settled down to the ACTUAL price (and released
//      entirely if the run never happened).

export async function POST(req: NextRequest, ctx: { params: { slug: string } }) {
  try {
    if (!flags.agents) throw new ApiError(503, 'disabled', 'agents are not enabled on this node');
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'use');
    enforceBurst(auth.accountId, { perMin: limits.rateUserPerMin, scope: 'agent-run' });

    const slug = decodeURIComponent(ctx.params.slug).trim().toLowerCase();
    const agent = await prisma.cloudAgent.findUnique({
      where: { slug },
      include: { function: { select: { id: true, slug: true, status: true, suspendedAt: true } } },
    });
    if (!agent || agent.ownerId !== auth.accountId) throw new ApiError(404, 'not_found', 'no such agent');
    if (agent.status === 'SUSPENDED') throw new ApiError(403, 'suspended', 'this agent was suspended by the platform');
    if (agent.status !== 'ACTIVE') {
      throw new ApiError(409, 'paused', `the agent is ${agent.status} — resume it with PATCH {"status":"ACTIVE"} first`);
    }
    if (agent.function.status !== 'PUBLISHED' || agent.function.suspendedAt) {
      throw new ApiError(409, 'function_not_live', 'the agent function has no active deployment');
    }

    let payload: unknown = {};
    const raw = await req.text();
    if (raw) {
      if (raw.length > limits.maxRequestBytes) {
        throw new ApiError(413, 'payload_too_large', `request body exceeds ${limits.maxRequestBytes} bytes`);
      }
      try {
        payload = JSON.parse(raw);
      } catch {
        throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
      }
    }

    const day = dayKeyUtc();
    // Reserve the per-run cap against the daily budget in ONE conditional statement. The CASE
    // handles the UTC day rollover; the WHERE makes the claim atomic under concurrency.
    const reserve = agent.dailySpendCapNanm > 0n ? agent.maxSpendPerRunNanm : 0n;
    if (reserve > 0n) {
      const claimed = await prisma.$executeRaw`
        UPDATE "CloudAgent"
        SET "spentTodayNanm" = (CASE WHEN "spendDayKey" = ${day} THEN "spentTodayNanm" ELSE 0 END) + ${reserve},
            "spendDayKey" = ${day}
        WHERE "id" = ${agent.id}
          AND (CASE WHEN "spendDayKey" = ${day} THEN "spentTodayNanm" ELSE 0 END) + ${reserve} <= "dailySpendCapNanm"`;
      if (claimed !== 1) {
        const fresh = await prisma.cloudAgent.findUnique({
          where: { id: agent.id },
          select: { spentTodayNanm: true, spendDayKey: true, dailySpendCapNanm: true },
        });
        const spent = fresh && fresh.spendDayKey === day ? fresh.spentTodayNanm : 0n;
        const e = new ApiError(
          402,
          'budget_exceeded',
          `daily spend cap reached: ${spent} of ${agent.dailySpendCapNanm} nANM used today (a run reserves up to ${reserve} nANM)`,
        );
        e.details = { spentTodayNanm: spent, dailySpendCapNanm: agent.dailySpendCapNanm, reserveNanm: reserve };
        throw e;
      }
    }

    let result;
    try {
      result = await invokeFunction({
        functionId: agent.function.id,
        payload,
        callerAccountId: auth.accountId,
        callerKind: 'agent',
        agentId: agent.id,
        maxSpendNanm: agent.maxSpendPerRunNanm > 0n ? agent.maxSpendPerRunNanm : null,
      });
    } catch (e) {
      // The run never produced an execution: release the reservation in full.
      if (reserve > 0n) {
        await prisma.$executeRaw`
          UPDATE "CloudAgent"
          SET "spentTodayNanm" = GREATEST((CASE WHEN "spendDayKey" = ${day} THEN "spentTodayNanm" ELSE 0 END) - ${reserve}, 0),
              "spendDayKey" = ${day}
          WHERE "id" = ${agent.id}`;
      }
      throw e;
    }

    // Settle the reservation down to what the run actually cost, and record the run.
    const actual = BigInt(result.receipt.grossNanm);
    const delta = actual - reserve;
    await prisma.$executeRaw`
      UPDATE "CloudAgent"
      SET "spentTodayNanm" = GREATEST((CASE WHEN "spendDayKey" = ${day} THEN "spentTodayNanm" ELSE 0 END) + ${delta}, 0),
          "spendDayKey" = ${day},
          "lastRunAt" = now(),
          "runsTotal" = "runsTotal" + 1
      WHERE "id" = ${agent.id}`;

    const after = await prisma.cloudAgent.findUnique({
      where: { id: agent.id },
      select: { spentTodayNanm: true, spendDayKey: true, dailySpendCapNanm: true, runsTotal: true },
    });

    return ok(
      {
        agent: agent.slug,
        requestId: result.requestId,
        status: result.status,
        result: result.result ?? null,
        error: result.error ?? null,
        durationMs: result.durationMs,
        receipt: result.receipt,
        budget: {
          spentTodayNanm: after && after.spendDayKey === day ? after.spentTodayNanm : actual,
          dailySpendCapNanm: agent.dailySpendCapNanm,
          runsTotal: after?.runsTotal ?? agent.runsTotal + 1,
        },
      },
      { status: result.status === 'succeeded' ? 200 : 502 },
    );
  } catch (e) {
    return err(e);
  }
}
